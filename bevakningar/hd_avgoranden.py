"""Högsta domstolen – nya avgöranden (prejudikat), inte prövningstillstånd.

Läser domstolarnas öppna API för "Sök rättspraxis"
(https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar) och plockar ut
publiceringar från HD där typ = PREJUDIKAT. Prövningstillstånd (typ
PROVNINGSTILLSTAND) och senare NJA-referat av redan rapporterade avgöranden
(publiceringsform REFERAT) hoppas över.

För varje nytt avgörande hämtas hela avgörandet (PDF) och sammanfattas av
Claude (Anthropics API, nyckeln claude_nyckel i config.local.json) eller, om
den saknas, av Gemini (gemini_nyckel). Sedan
  1. kommer en ntfy-notis med en kort sammanfattning, och
  2. skrivs hela sammanfattningen in överst i Google-dokumentet
     "Claude HD-bevakning" (nycklarna hd_dokument_url och hd_dokument_nyckel).
Utan nyckel används HD:s egen korta sammanfattning.

Fristående kommandon:
  ./venv/bin/python bevakningar/hd_avgoranden.py --senaste
        visar de tre senaste avgörandena med HD:s egna sammanfattningar
  ./venv/bin/python bevakningar/hd_avgoranden.py --testa
        sammanfattar det senaste avgörandet och skriver ut resultatet
  ./venv/bin/python bevakningar/hd_avgoranden.py --fyll-pa 2026-09-01
        skriver in alla avgöranden sedan datumet i dokumentet (inga notiser).
        Ett avgörande som redan står i dokumentet ersätts.
"""

import base64
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NAMN = "HD – nytt avgörande"
INTERVALL_MINUTER = 24 * 60  # en gång per dygn
AKTIV = True
BEHOVER_WEBBLASARE = False

# Bara avgöranden publicerade från och med bevakningens start räknas som nya,
# så att det inte kommer en klump gamla avgöranden vid första körningen.
STARTTID = "2026-10-02T00:00:00"

BAS = "https://rattspraxis.etjanst.domstol.se"
API = BAS + "/api/v1/publiceringar"
PDF = BAS + "/api/v1/bilagor/{fil}"
LANK = BAS + "/sok/publicering/{id}"
DOKUMENT_LANK = "https://docs.google.com/document/d/1BfICoZw3f6HjhouSF0TIlvYgds7u2V0wIp_z_RRclHs/edit"

CLAUDE = "https://api.anthropic.com/v1/messages"
CLAUDE_MODELL = "claude-opus-5-5"  # kan ändras med claude_modell i config.local.json

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{modell}:generateContent"
# Provas i tur och ordning om ingen modell anges i config.local.json (gemini_modell).
GEMINI_MODELLER = ["gemini-pro-latest", "gemini-2.5-pro", "gemini-flash-latest", "gemini-2.5-flash"]

ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
DOKUMENT_TILLSTAND = ROT / "state" / "hd_dokument.json"
SAMMANFATTNINGAR = ROT / "state" / "hd_sammanfattningar.json"

MANADER = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]

INSTRUKTION = """Du är en erfaren svensk jurist. Bifogat är ett avgörande från Högsta domstolen.
Skriv en sammanfattning för en hovrättsdomare som vill förstå avgörandet ordentligt utan att läsa det.

Svara ENBART med ett JSON-objekt med följande nycklar (alla värden är strängar, alla nycklar ska finnas):
- "kort": 2–3 meningar: vilken rättsfråga HD prövade, hur HD besvarade den och utgången.
- "fraga": rättsfrågan eller rättsfrågorna som HD prövade, i 2–4 meningar. Föregrip inte
  HD:s resonemang – det hör hemma under "bedomning".
- "bakgrund": det nödvändigaste om omständigheterna och hur underinstanserna bedömde saken,
  i högst 2–3 meningar. Inga detaljer om brotten utöver vad som behövs för att förstå rättsfrågan.
- "bedomning": HD:s bärande skäl i den ordning HD resonerar. Ange de lagrum, förarbeten och
  rättsfall som HD bygger på och hänvisa till punkter i avgörandet (t.ex. "p. 14").
  Detta är huvuddelen och ska vara utförlig: ta med varje prejudikatbärande ställningstagande.
  Dela upp i stycken med en tom rad emellan.
- "utgang": domslutet eller beslutet. Utelämna rättegångskostnader, ersättning till
  försvarare och liknande om de inte är en del av rättsfrågan.
- "betydelse": vad avgörandet innebär för rättstillämpningen, t.ex. om praxis klargörs,
  ändras eller utvecklas.
- "skiljaktiga": skiljaktiga meningar eller tillägg, med kort vad de ansåg; tom sträng om inga finns.

Skriv på saklig juridisk svenska utan punktlistor och utan kommentarer om vad avgörandet
inte anger. Skriv "fått laga kraft", aldrig "vunnit laga kraft". Lägg inte till något
som inte framgår av avgörandet."""


# --- Hämta från domstolens API --------------------------------------------------

def _hamta(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "pi-bevakningar"})
    with urllib.request.urlopen(req, timeout=timeout) as svar:
        return svar.read()


def _hamta_publiceringar(antal=100):
    parametrar = urllib.parse.urlencode({
        "domstolkod": "HDO",
        "pagesize": antal,
        "sortorder": "publiceringstid",
        "asc": "false",
    })
    data = json.loads(_hamta(f"{API}?{parametrar}").decode("utf-8"))
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
    pdf = None
    for b in p.get("bilagaLista") or []:
        if b.get("fillagringId") and (b.get("filnamn") or "").lower().endswith(".pdf"):
            pdf = PDF.format(fil=urllib.parse.quote(b["fillagringId"], safe=""))
            break
    return {
        "id": p["id"],
        "malnummer": ", ".join(p.get("malNummerLista") or []) or "okänt målnummer",
        "benamning": benamning,
        "avgorandedatum": _datum(p.get("avgorandedatum")),
        "publicerat": (p.get("publiceringstid") or "").replace("T", " ")[:16],
        "hd_sammanfattning": (p.get("sammanfattning") or "").strip(),
        "nyckelord": p.get("nyckelordLista") or [],
        "lagrum": [l.get("referens", "") for l in (p.get("lagrumLista") or []) if l.get("referens")],
        "lank": LANK.format(id=p["id"]),
        "pdf": pdf,
    }


# --- Konfiguration och tillstånd -----------------------------------------------

def _konfig():
    try:
        return json.loads(KONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _las(sokvag, standard):
    try:
        return json.loads(sokvag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return standard


def _spara(sokvag, data):
    sokvag.parent.mkdir(exist_ok=True)
    tmp = sokvag.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(sokvag)


# --- Sammanfattning med Claude eller Gemini ------------------------------------------

def _tolka_json(text):
    text = text.strip()
    start, slut = text.find("{"), text.rfind("}")
    if start < 0 or slut < start:
        raise RuntimeError(f"svaret var inte JSON: {text[:200]}")
    return json.loads(text[start:slut + 1])


def _claude(konfig, pdf_data):
    modell = konfig.get("claude_modell") or CLAUDE_MODELL
    kropp = json.dumps({
        "model": modell,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                             "data": base64.b64encode(pdf_data).decode("ascii")}},
            {"type": "text", "text": INSTRUKTION},
        ]}],
    }).encode("utf-8")
    # Personliga nycklar (sk-ant-usr-…) skickas som Bearer-token; det fungerar även för äldre nycklar.
    huvuden = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
               "Authorization": "Bearer " + konfig["claude_nyckel"].strip()}
    if konfig.get("claude_arbetsyta"):  # krävs om nyckeln inte är knuten till en arbetsyta
        huvuden["anthropic-workspace-id"] = konfig["claude_arbetsyta"].strip()
    req = urllib.request.Request(
        CLAUDE, data=kropp, method="POST",
        headers=huvuden,
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as svar:
            data = json.loads(svar.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Claude-nyckeln godtas inte längre – den har troligen gått ut. "
                               "Skapa en ny på platform.claude.com/settings/keys och lägg in den på Pi:n")
        raise RuntimeError(f"Claude ({modell}) svarade {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if data.get("stop_reason") == "max_tokens":
        raise RuntimeError("svaret från Claude blev för långt och klipptes av")
    resultat = _tolka_json(text)
    resultat["modell"] = modell
    return resultat


def _gemini(konfig, pdf_data):
    nyckel = konfig["gemini_nyckel"]
    modeller = [konfig["gemini_modell"]] if konfig.get("gemini_modell") else GEMINI_MODELLER
    kropp = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "application/pdf",
                             "data": base64.b64encode(pdf_data).decode("ascii")}},
            {"text": INSTRUKTION},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }).encode("utf-8")

    senaste_fel = None
    for modell in modeller:
        req = urllib.request.Request(
            GEMINI.format(modell=modell), data=kropp, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": nyckel},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as svar:
                data = json.loads(svar.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            senaste_fel = f"Gemini ({modell}) svarade {e.code}: {text}"
            if e.code == 404:  # modellen finns inte – prova nästa
                continue
            raise RuntimeError(senaste_fel)
        delar = data["candidates"][0]["content"]["parts"]
        text = "".join(d.get("text", "") for d in delar if not d.get("thought"))
        resultat = _tolka_json(text)
        resultat["modell"] = modell
        return resultat
    raise RuntimeError(senaste_fel or "ingen Gemini-modell att prova")


def _sammanfatta(konfig, u, forsok=3):
    """Returnerar Geminis sammanfattning (dict) eller kastar fel."""
    if not u["pdf"]:
        raise RuntimeError("avgörandet saknar PDF i Sök rättspraxis")
    pdf_data = _hamta(u["pdf"], timeout=120)
    motor = _claude if konfig.get("claude_nyckel") else _gemini
    for i in range(forsok):
        try:
            return motor(konfig, pdf_data)
        except Exception:
            if i == forsok - 1:
                raise
            time.sleep(30 * (i + 1))


def _med_sammanfattning(konfig, u, cache):
    """Lägger till sammanfattning i u. Använder cache så att Gemini bara anropas en gång."""
    if u["id"] in cache:
        u["sammanfattning"] = cache[u["id"]]
        return u
    if not (konfig.get("claude_nyckel") or konfig.get("gemini_nyckel")):
        u["sammanfattning"] = None
        return u
    try:
        u["sammanfattning"] = _sammanfatta(konfig, u)
    except Exception as e:
        u["sammanfattning"] = None
        u["fel"] = f"Den automatiska sammanfattningen misslyckades ({str(e)[:150]})."
        return u  # cachas inte – nästa körning försöker igen
    cache[u["id"]] = u["sammanfattning"]
    return u


# --- Notis och dokument -----------------------------------------------------------

def _rubrik(u):
    return u["malnummer"] + (f" – ”{u['benamning']}”" if u["benamning"] else "")


def _notistext(u):
    s = u.get("sammanfattning")
    if s and s.get("kort"):
        text = s["kort"].strip()
        tillagg = "Hela sammanfattningen finns i dokumentet Claude HD-bevakning."
    else:
        text = u["hd_sammanfattning"] or "(HD har inte lagt in någon sammanfattning.)"
        tillagg = u.get("fel") or ""
    if len(text) > 900:
        text = text[:900].rsplit(" ", 1)[0] + " …"
    rader = [_rubrik(u), text]
    if tillagg:
        rader.append(tillagg)
    rader.append(f"Avgjort {u['avgorandedatum']}.\n")
    return "\n".join(rader)


def _block(u):
    """Posten i dokumentet, som en lista block som Apps Script-kopplingen ritar upp."""
    b = [{"typ": "rubrik", "text": _rubrik(u)}]
    meta = f"Avgjort {u['avgorandedatum']} · publicerat {u['publicerat']}"
    if u["nyckelord"]:
        meta += " · " + ", ".join(u["nyckelord"])
    b.append({"typ": "meta", "text": meta})
    s = u.get("sammanfattning")
    if s:
        for nyckel, etikett in [("kort", "I korthet"), ("fraga", "Frågan"), ("bakgrund", "Bakgrund"),
                                ("bedomning", "HD:s bedömning"), ("utgang", "Utgång"),
                                ("betydelse", "Betydelse"), ("skiljaktiga", "Skiljaktiga och tillägg")]:
            text = (s.get(nyckel) or "").strip()
            if text:
                b.append({"typ": "avsnitt", "etikett": etikett, "text": text})
        b.append({"typ": "meta", "text": f"HD:s egen sammanfattning: {u['hd_sammanfattning']}"})
    else:
        b.append({"typ": "text", "text": u["hd_sammanfattning"] or "(HD har inte lagt in någon sammanfattning.)"})
        if u.get("fel"):
            b.append({"typ": "meta", "text": u["fel"]})
    if u["lagrum"]:
        b.append({"typ": "meta", "text": "Lagrum: " + "; ".join(u["lagrum"])})
    if u["pdf"]:
        b.append({"typ": "lank", "text": "Avgörandet (PDF)", "url": u["pdf"]})
    b.append({"typ": "lank", "text": "Öppna i Sök rättspraxis", "url": u["lank"]})
    return b


def _skriv_i_dokument(konfig, u):
    """Skickar ett avgörande till Apps Script-kopplingen i dokumentet."""
    data = {"nyckel": konfig["hd_dokument_nyckel"], "malnummer": u["malnummer"], "block": _block(u)}
    req = urllib.request.Request(
        konfig["hd_dokument_url"],
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Google svarar med en omdirigering som urllib följer med GET – det är meningen.
    with urllib.request.urlopen(req, timeout=60) as svar:
        text = svar.read().decode("utf-8", "replace").strip()
    if text == "ok":
        raise RuntimeError("koden i dokumentets Apps Script är den gamla – byt koden och "
                           "implementera en ny version (Hantera implementeringar → Ny version)")
    if text != "ok:2":
        raise RuntimeError(f"dokumentet svarade: {text[:200]}")


def _dokument_installt(konfig):
    return bool(konfig.get("hd_dokument_url") and konfig.get("hd_dokument_nyckel"))


# --- Bevakningen -----------------------------------------------------------------

def kontrollera(webblasare):
    test = "--test" in sys.argv
    konfig = _konfig()
    cache = _las(SAMMANFATTNINGAR, {})
    dok = _las(DOKUMENT_TILLSTAND, {"skrivna": [], "vantar": {}})
    dok.pop("vantar", None)  # äldre format

    avgoranden = [
        _uppgifter(p)
        for p in _hamta_publiceringar()
        if _ar_avgorande(p) and (p.get("publiceringstid") or "") >= STARTTID
    ]
    avgoranden.sort(key=lambda u: u["publicerat"])  # äldst först – nyast hamnar överst i dokumentet

    fynd, problem = [], []
    for u in avgoranden:
        _med_sammanfattning(konfig, u, cache)
        if u.get("fel") and u["pdf"]:  # saknad PDF är inget fel att varna för varje dag
            problem.append(u["fel"])
        fynd.append({"id": u["id"], "text": _notistext(u), "url": DOKUMENT_LANK if u.get("sammanfattning") else u["lank"]})

        if test or not _dokument_installt(konfig):
            continue
        # Skrivs om när den riktiga sammanfattningen kommit, om förra försöket fick nöja sig med HD:s.
        status = "full" if u.get("sammanfattning") else "kort"
        tidigare = {s.split(":")[0]: s for s in dok["skrivna"]}.get(u["id"])
        if tidigare in (f"{u['id']}:full",) or (tidigare == f"{u['id']}:kort" and status == "kort"):
            continue
        try:
            _skriv_i_dokument(konfig, u)
            dok["skrivna"] = [s for s in dok["skrivna"] if not s.startswith(u["id"])] + [f"{u['id']}:{status}"]
        except Exception as e:
            problem.append(f"Kunde inte skriva i dokumentet ({str(e)[:150]}).")

    if not test:
        # Spara bara sammanfattningar för avgöranden som fortfarande finns i listan.
        aktuella = {u["id"] for u in avgoranden}
        _spara(SAMMANFATTNINGAR, {k: v for k, v in cache.items() if k in aktuella})
        dok["skrivna"] = dok["skrivna"][-500:]
        _spara(DOKUMENT_TILLSTAND, dok)

    if problem:
        # Ett id per dag ger högst en varning per dygn; nästa körning försöker igen.
        fynd.append({
            "id": "hd-problem-" + time.strftime("%Y-%m-%d"),
            "text": "⚠️ " + " ".join(dict.fromkeys(problem)) + " Försöker igen vid nästa körning.",
        })
    return fynd


# --- Fristående kommandon -----------------------------------------------------------

def _main():
    konfig = _konfig()
    alla = [_uppgifter(p) for p in _hamta_publiceringar() if _ar_avgorande(p)]
    if not alla:
        print("Hittade inga avgöranden från HD i API:et.")
        return

    if "--testa" in sys.argv:
        if not (konfig.get("claude_nyckel") or konfig.get("gemini_nyckel")):
            print("claude_nyckel saknas i config.local.json – se README.")
            sys.exit(1)
        u = alla[0]
        print(f"Sammanfattar {u['malnummer']} … (kan ta en minut)")
        s = _sammanfatta(konfig, u, forsok=1)
        print(f"[modell: {s.pop('modell', '?')}]\n")
        for nyckel, varde in s.items():
            print(f"== {nyckel} ==\n{varde}\n")
        return

    if "--fyll-pa" in sys.argv:
        try:
            fran = sys.argv[sys.argv.index("--fyll-pa") + 1]
            dt.date.fromisoformat(fran)
        except (IndexError, ValueError):
            print("Ange datum, t.ex.: --fyll-pa 2026-09-01")
            sys.exit(1)
        if not _dokument_installt(konfig):
            print("hd_dokument_url eller hd_dokument_nyckel saknas i config.local.json – se README.")
            sys.exit(1)
        valda = sorted((u for u in alla if u["publicerat"][:10] >= fran), key=lambda u: u["publicerat"])
        print(f"{len(valda)} avgöranden sedan {fran}.")
        cache = _las(SAMMANFATTNINGAR, {})
        dok = _las(DOKUMENT_TILLSTAND, {"skrivna": []})
        dok.pop("vantar", None)
        for u in valda:
            print(f"  {u['malnummer']} …", end=" ", flush=True)
            _med_sammanfattning(konfig, u, cache)
            _skriv_i_dokument(konfig, u)
            status = "full" if u.get("sammanfattning") else "kort"
            dok["skrivna"] = [s for s in dok["skrivna"] if not s.startswith(u["id"])] + [f"{u['id']}:{status}"]
            _spara(SAMMANFATTNINGAR, cache)
            _spara(DOKUMENT_TILLSTAND, dok)
            print("klar" if u.get("sammanfattning") else f"skrevs med HD:s korta sammanfattning. {u.get('fel', '')}")
        print("Klart. Titta i Google Docs.")
        return

    for u in alla[:3]:
        print(_notistext(u))
        print(u["lank"])
        print("-" * 60)


if __name__ == "__main__":
    _main()
