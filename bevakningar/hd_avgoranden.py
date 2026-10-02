"""Högsta domstolen – nya avgöranden (prejudikat), inte prövningstillstånd.

Läser domstolarnas öppna API för "Sök rättspraxis"
(https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar) och plockar ut
publiceringar från HD där typ = PREJUDIKAT. Prövningstillstånd (typ
PROVNINGSTILLSTAND) och senare NJA-referat av redan rapporterade avgöranden
(publiceringsform REFERAT) hoppas över.

Varje nytt avgörande
  1. ger en ntfy-notis med målnummer, benämning och HD:s sammanfattning, och
  2. skrivs in i Google-dokumentet "Claude HD-bevakning", om dokumentkopplingen
     är inställd (nycklarna hd_dokument_url och hd_dokument_nyckel i
     config.local.json, se README). Saknas kopplingen kommer bara notisen.

Kan också köras fristående för att testa:
  ./venv/bin/python bevakningar/hd_avgoranden.py --senaste     visar de tre senaste avgörandena
  ./venv/bin/python bevakningar/hd_avgoranden.py --provskriv   skriver det senaste i dokumentet
"""

import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

NAMN = "HD – nytt avgörande"
INTERVALL_MINUTER = 15
AKTIV = True
BEHOVER_WEBBLASARE = False

# Bara avgöranden publicerade från och med bevakningens start räknas som nya,
# så att det inte kommer en klump gamla avgöranden vid första körningen.
STARTTID = "2026-10-02T00:00:00"

API = "https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar"
LANK = "https://rattspraxis.etjanst.domstol.se/sok/publicering/{id}"

ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
DOKUMENT_TILLSTAND = ROT / "state" / "hd_dokument.json"

MANADER = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]


def _hamta_publiceringar(antal=40):
    parametrar = urllib.parse.urlencode({
        "domstolkod": "HDO",
        "pagesize": antal,
        "sortorder": "publiceringstid",
        "asc": "false",
    })
    req = urllib.request.Request(
        f"{API}?{parametrar}",
        headers={"Accept": "application/json", "User-Agent": "pi-bevakningar"},
    )
    with urllib.request.urlopen(req, timeout=60) as svar:
        data = json.loads(svar.read().decode("utf-8"))
    if isinstance(data, dict):  # tål om API:et skulle börja svara med ett omslag
        data = data.get("publiceringLista") or data.get("content") or []
    return data


def _ar_avgorande(p):
    return (
        p.get("domstol", {}).get("domstolKod") == "HDO"
        and p.get("typ") == "PREJUDIKAT"
        and p.get("publiceringsform") != "REFERAT"
    )


def _datum(iso):
    try:
        d = dt.date.fromisoformat(iso[:10])
        return f"{d.day} {MANADER[d.month - 1]} {d.year}"
    except (TypeError, ValueError):
        return iso or "okänt datum"


def _uppgifter(p):
    benamning = (p.get("benamning") or "").strip().strip('"”“').strip()
    return {
        "id": p["id"],
        "malnummer": ", ".join(p.get("malNummerLista") or []) or "okänt målnummer",
        "benamning": benamning,
        "avgorandedatum": _datum(p.get("avgorandedatum")),
        "publicerat": (p.get("publiceringstid") or "").replace("T", " ")[:16],
        "sammanfattning": (p.get("sammanfattning") or "").strip(),
        "nyckelord": p.get("nyckelordLista") or [],
        "lagrum": [l.get("referens", "") for l in (p.get("lagrumLista") or []) if l.get("referens")],
        "lank": LANK.format(id=p["id"]),
    }


def _notistext(u):
    rubrik = u["malnummer"] + (f" ”{u['benamning']}”" if u["benamning"] else "")
    sammanfattning = u["sammanfattning"] or "(HD har inte lagt in någon sammanfattning.)"
    if len(sammanfattning) > 700:
        sammanfattning = sammanfattning[:700].rsplit(" ", 1)[0] + " …"
    rader = [rubrik, sammanfattning]
    if u["nyckelord"]:
        rader.append("Nyckelord: " + ", ".join(u["nyckelord"]))
    rader.append(f"Avgjort {u['avgorandedatum']}.\n")
    return "\n".join(rader)


# --- Google-dokumentet -------------------------------------------------------

def _konfig():
    try:
        return json.loads(KONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _las_dokumenttillstand():
    try:
        return json.loads(DOKUMENT_TILLSTAND.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"skrivna": [], "vantar": {}}


def _spara_dokumenttillstand(t):
    DOKUMENT_TILLSTAND.parent.mkdir(exist_ok=True)
    t["skrivna"] = t["skrivna"][-500:]
    tmp = DOKUMENT_TILLSTAND.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DOKUMENT_TILLSTAND)


def _skriv_i_dokument(konfig, u):
    """Skickar ett avgörande till Apps Script-kopplingen i dokumentet."""
    data = dict(u, nyckel=konfig["hd_dokument_nyckel"])
    req = urllib.request.Request(
        konfig["hd_dokument_url"],
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Google svarar med en omdirigering som urllib följer med GET – det är meningen.
    with urllib.request.urlopen(req, timeout=60) as svar:
        text = svar.read().decode("utf-8", "replace").strip()
    if text != "ok":
        raise RuntimeError(f"dokumentet svarade: {text[:200]}")


def _uppdatera_dokument(avgoranden, test):
    """Skriver nya avgöranden i dokumentet. Returnerar ev. feltext."""
    konfig = _konfig()
    if not (konfig.get("hd_dokument_url") and konfig.get("hd_dokument_nyckel")):
        return None  # dokumentkopplingen är inte inställd – bara notiser
    if test:
        return None  # testkörningar skriver inte i dokumentet

    t = _las_dokumenttillstand()
    skrivna = set(t["skrivna"])
    for u in avgoranden:
        if u["id"] not in skrivna and u["id"] not in t["vantar"]:
            t["vantar"][u["id"]] = u

    fel = None
    # Äldst först, så att dokumentet får rätt ordning (nyast hamnar överst).
    for id_, u in sorted(t["vantar"].items(), key=lambda kv: kv[1]["publicerat"]):
        try:
            _skriv_i_dokument(konfig, u)
        except Exception as e:
            fel = str(e)[:200]
            break
        t["skrivna"].append(id_)
        del t["vantar"][id_]
    _spara_dokumenttillstand(t)
    return fel


# --- Bevakningen ---------------------------------------------------------------

def kontrollera(webblasare):
    test = "--test" in sys.argv
    avgoranden = [
        _uppgifter(p)
        for p in _hamta_publiceringar()
        if _ar_avgorande(p) and (p.get("publiceringstid") or "") >= STARTTID
    ]

    fynd = [{"id": u["id"], "text": _notistext(u), "url": u["lank"]} for u in avgoranden]

    fel = _uppdatera_dokument(avgoranden, test)
    if fel:
        # Ett id per dag ger högst en varning per dygn; nästa körning försöker igen.
        fynd.append({
            "id": "dokumentfel-" + time.strftime("%Y-%m-%d"),
            "text": f"⚠️ Kunde inte skriva i dokumentet Claude HD-bevakning ({fel}). Försöker igen.",
        })
    return fynd


def _main():
    senaste = [_uppgifter(p) for p in _hamta_publiceringar() if _ar_avgorande(p)]
    if not senaste:
        print("Hittade inga avgöranden från HD i API:et.")
        return
    if "--provskriv" in sys.argv:
        konfig = _konfig()
        if not konfig.get("hd_dokument_url"):
            print("hd_dokument_url saknas i config.local.json – se README.")
            sys.exit(1)
        _skriv_i_dokument(konfig, senaste[0])
        print(f"Skrev {senaste[0]['malnummer']} i dokumentet. Titta i Google Docs.")
        return
    for u in senaste[:3]:
        print(_notistext(u))
        print(u["lank"])
        print("-" * 60)


if __name__ == "__main__":
    _main()
