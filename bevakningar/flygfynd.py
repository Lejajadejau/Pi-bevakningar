"""Flygfynd – helgresor i Europa för familjen (4 personer).

Letar efter riktigt billiga tur och retur-resor från Arlanda och Skavsta:
ut lördag förmiddag, hem söndag eftermiddag. Notis kommer bara när totalpriset
för fyra personer är under gränsen (standard 4 000 kr). Annars hörs ingenting.

Så här går det till (SerpApi, som hämtar Google Flights):
1. Google "Explore" ger, för en viss helg, de billigaste resmålen i Europa
   från ARN/NYO – en sökning per helg täcker alla städer.
2. Resmål som ser billiga ut kontrolleras med en riktig sökning för fyra
   vuxna med tidsfönster (lördag 05–12 ut, söndag 14.30–21 hem), plus en
   sökning på hemresan. Först då blir det ett fynd.

Gratisnivån hos SerpApi är 250 sökningar i månaden. Bevakningen sprider
helgerna över dygnen och stannar om kontot börjar ta slut.

Ruth och Klas räknas som vuxna (barnpris gäller oftast bara under 12 år).

Inställningar i config.local.json:
  "serpapi_nyckel": "din nyckel från serpapi.com"          (krävs)
  "flygfynd_grans_kr": 4000                                 (valfritt)
  "flygfynd_direkt": true                                   (valfritt, bara direktflyg)

Fristående test (söker nästa helg, skriver ut, ingen notis):
  ./venv/bin/python bevakningar/flygfynd.py --testa
"""

import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

NAMN = "Flygfynd"
INTERVALL_MINUTER = 24 * 60
AKTIV = True
BEHOVER_WEBBLASARE = False
PRIORITET = "high"

AVRESA = "ARN,NYO"              # Arlanda och Skavsta
EUROPA = "/m/02j9z"             # Google-id för Europa
RESENARER = 4
STANDARD_GRANS_KR = 4000
UT_TIDER = (5, 12)              # lördag, avgång mellan 05 och 12
HEM_TIDER = (14, 21)            # söndag, avgång mellan 14.30 och 21
HEM_TIDIGAST = "14:30"          # tidigaste avgång hem (finare än hela timmar)
ANTAL_HELGER = 8                # cirka två månader framåt
HELGER_PER_KORNING = 4          # hälften varje dygn → varje helg varannan dag
MAX_KONTROLLER_PER_KORNING = 3  # fördjupade sökningar per körning
RESERV_SOKNINGAR = 15           # stanna när så här få sökningar återstår
FYND_GILTIGT_DAGAR = 5          # ett fynd visas så länge det kontrollerats nyligen

STOCKHOLM = ZoneInfo("Europe/Stockholm")
ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
TILLSTAND = ROT / "state" / "flygfynd.json"
API = "https://serpapi.com/search.json"
KONTO_API = "https://serpapi.com/account.json"

MANADER = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]
FLYGPLATSER = {"ARN": "Arlanda", "NYO": "Skavsta"}


def _konfig():
    try:
        return json.loads(KONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _las_tillstand():
    try:
        return json.loads(TILLSTAND.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _spara_tillstand(t):
    if "--test" in sys.argv:
        return
    TILLSTAND.parent.mkdir(exist_ok=True)
    tmp = TILLSTAND.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TILLSTAND)


def _hamta(url, parametrar):
    adress = url + "?" + urllib.parse.urlencode(parametrar)
    req = urllib.request.Request(adress, headers={"User-Agent": "pi-bevakningar"})
    with urllib.request.urlopen(req, timeout=90) as svar:
        data = json.loads(svar.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(f"SerpApi: {data['error']}")
    return data


class Sokare:
    def __init__(self, nyckel):
        self.nyckel = nyckel
        self.antal = 0

    def kvar(self):
        # Kontouppgifterna räknas inte som en sökning.
        konto = _hamta(KONTO_API, {"api_key": self.nyckel})
        return konto.get("total_searches_left", konto.get("plan_searches_left", 0))

    def sok(self, **p):
        self.antal += 1
        p.update({"api_key": self.nyckel, "currency": "SEK", "gl": "se", "hl": "sv"})
        return _hamta(API, p)


def _helger(idag):
    """Kommande lördagar, minst tre dagar bort."""
    lordag = idag + dt.timedelta(days=(5 - idag.weekday()) % 7)
    if (lordag - idag).days < 3:
        lordag += dt.timedelta(days=7)
    return [lordag + dt.timedelta(days=7 * i) for i in range(ANTAL_HELGER)]


def _tal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        siffror = "".join(c for c in v if c.isdigit())
        return float(siffror) if siffror else None
    return None


def _timme(tidstext):
    # "2026-11-14 07:35" → 7
    try:
        return int(tidstext.split(" ")[1].split(":")[0])
    except Exception:
        return None


def _klockslag(tidstext):
    try:
        return tidstext.split(" ")[1]
    except Exception:
        return "?"


def _explore(sokare, lordag, direkt):
    sondag = lordag + dt.timedelta(days=1)
    p = dict(engine="google_travel_explore", departure_id=AVRESA, arrival_area_id=EUROPA,
             outbound_date=lordag.isoformat(), return_date=sondag.isoformat(), adults=1)
    if direkt:
        p["stops"] = 1
    data = sokare.sok(**p)
    kandidater = []
    for d in data.get("destinations") or []:
        pris = _tal(d.get("flight_price"))
        kod = (d.get("destination_airport") or {}).get("code")
        if pris and kod:
            kandidater.append({"stad": d.get("name") or kod, "land": d.get("country", ""),
                               "kod": kod, "pris_pp": pris})
    return sorted(kandidater, key=lambda k: k["pris_pp"])


def _inom(tid, fonster, tidigast=None):
    h = _timme(tid)
    if h is None or not (fonster[0] <= h < fonster[1]):
        return False
    return tidigast is None or _klockslag(tid) >= tidigast


def _kontrollera(sokare, lordag, kand, direkt):
    """Riktig sökning för fyra personer. Returnerar ett fynd eller None."""
    sondag = lordag + dt.timedelta(days=1)
    p = dict(engine="google_flights", type=1, departure_id=AVRESA, arrival_id=kand["kod"],
             outbound_date=lordag.isoformat(), return_date=sondag.isoformat(),
             adults=RESENARER, sort_by=2,
             outbound_times=f"{UT_TIDER[0]},{UT_TIDER[1]}",
             return_times=f"{HEM_TIDER[0]},{HEM_TIDER[1]}")
    if direkt:
        p["stops"] = 1
    data = sokare.sok(**p)
    alternativ = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    basta = None
    for alt in alternativ:
        ben = alt.get("flights") or []
        pris = _tal(alt.get("price"))
        if not ben or not pris:
            continue
        if not _inom(ben[0]["departure_airport"].get("time", ""), UT_TIDER):
            continue
        if basta is None or pris < basta[0]:
            basta = (pris, alt)
    if not basta:
        return None
    pris, alt = basta

    # Google anger priset för alla resenärer; skydd om det ändå skulle vara per person.
    total = pris if pris >= 2.5 * kand["pris_pp"] else pris * RESENARER

    ut = alt["flights"]
    hem_text = "hem sön eftermiddag"
    if alt.get("departure_token") and sokare is not None:
        try:
            retur = sokare.sok(engine="google_flights", type=1, departure_id=AVRESA,
                               arrival_id=kand["kod"], outbound_date=lordag.isoformat(),
                               return_date=sondag.isoformat(), adults=RESENARER,
                               departure_token=alt["departure_token"])
            hem = [a for a in (retur.get("best_flights") or []) + (retur.get("other_flights") or [])
                   if a.get("flights") and _inom(a["flights"][0]["departure_airport"].get("time", ""), HEM_TIDER, HEM_TIDIGAST)]
            if not hem:
                return None  # ingen hemresa på söndagseftermiddagen till det priset
            hem.sort(key=lambda a: _tal(a.get("price")) or 1e9)
            hb = hem[0]["flights"]
            hem_text = f"hem sön {_klockslag(hb[0]['departure_airport']['time'])} ({hb[0].get('airline', '')})"
            hempris = _tal(hem[0].get("price"))
            if hempris:
                total = hempris if hempris >= 2.5 * kand["pris_pp"] else hempris * RESENARER
        except Exception:
            pass  # behåll uppgiften från första sökningen

    fran = FLYGPLATSER.get(ut[0]["departure_airport"].get("id"), ut[0]["departure_airport"].get("id", ""))
    lank = data.get("search_metadata", {}).get("google_flights_url") or "https://www.google.com/travel/flights"
    return {
        "total": round(total),
        "text_ut": f"ut lör {_klockslag(ut[0]['departure_airport']['time'])} från {fran} ({ut[0].get('airline', '')})",
        "text_hem": hem_text,
        "url": lank,
    }


def _kr(belopp):
    return f"{belopp:,} kr".replace(",", " ")


def _datum(d):
    return f"{d.day} {MANADER[d.month - 1]}"


def kontrollera(_webblasare=None, test=False):
    konfig = _konfig()
    nyckel = (konfig.get("serpapi_nyckel") or "").strip()
    if not nyckel:
        raise RuntimeError("serpapi_nyckel saknas i config.local.json")
    grans = float(konfig.get("flygfynd_grans_kr", STANDARD_GRANS_KR))
    direkt = konfig.get("flygfynd_direkt", True)
    sokare = Sokare(nyckel)
    tillstand = _las_tillstand()
    idag = dt.datetime.now(STOCKHOLM).date()

    kvar = sokare.kvar()
    helger = _helger(idag)
    if test:
        helger = helger[:1]
    else:
        omgang = tillstand.get("omgang", 0)
        helger = helger[omgang % 2::2][:HELGER_PER_KORNING]
        tillstand["omgang"] = omgang + 1

    fynd = {k: v for k, v in tillstand.get("fynd", {}).items()
            if dt.date.fromisoformat(v["lordag"]) >= idag + dt.timedelta(days=2)
            and time.time() - v["kontrollerad"] < FYND_GILTIGT_DAGAR * 86400}

    kontroller = 0
    for lordag in helger:
        if kvar - sokare.antal <= RESERV_SOKNINGAR:
            print(f"Flygfynd: bara {kvar - sokare.antal} sökningar kvar hos SerpApi – pausar.")
            break
        kandidater = _explore(sokare, lordag, direkt)
        if test:
            print(f"Helgen {lordag}: {len(kandidater)} resmål. Billigast per person:")
            for k in kandidater[:8]:
                print(f"  {k['stad']} ({k['kod']}): {k['pris_pp']:.0f} kr")
        for kand in kandidater:
            if kand["pris_pp"] * RESENARER > grans:
                break  # sorterat på pris – resten är dyrare
            if kontroller >= MAX_KONTROLLER_PER_KORNING or kvar - sokare.antal <= RESERV_SOKNINGAR + 2:
                break
            kontroller += 1
            res = _kontrollera(sokare, lordag, kand, direkt)
            if test:
                print(f"  Kontroll {kand['stad']}: {res}")
            nyckel_fynd = f"{lordag.isoformat()}-{kand['kod']}"
            if res and res["total"] <= grans:
                fynd[nyckel_fynd] = {"lordag": lordag.isoformat(), "stad": kand["stad"],
                                     "land": kand["land"], "kontrollerad": time.time(), **res}
            else:
                fynd.pop(nyckel_fynd, None)

    tillstand["fynd"] = fynd
    tillstand["senast_kvar"] = kvar - sokare.antal
    _spara_tillstand(tillstand)

    resultat = []
    for nyckel_fynd, f in sorted(fynd.items(), key=lambda x: x[1]["total"]):
        lordag = dt.date.fromisoformat(f["lordag"])
        sondag = lordag + dt.timedelta(days=1)
        resultat.append({
            "id": nyckel_fynd,
            "titel": f"✈️ Fynd: {f['stad']} {_datum(lordag)}–{_datum(sondag)}, {_kr(f['total'])}",
            "text": (f"{f['stad']}, {f['land']} {_datum(lordag)}–{_datum(sondag)}: "
                     f"{_kr(f['total'])} för fyra tur och retur. "
                     f"{f['text_ut']}, {f['text_hem']}."),
            "url": f["url"],
        })
    return resultat


if __name__ == "__main__":
    if "--testa" in sys.argv:
        sys.argv.append("--test")
        k = _konfig()
        if not k.get("serpapi_nyckel"):
            sys.exit("Lägg först in serpapi_nyckel i config.local.json.")
        print(f"Sökningar kvar hos SerpApi: {Sokare(k['serpapi_nyckel'].strip()).kvar()}")
        for f in kontrollera(test=True):
            print("FYND:", f["text"], f["url"])
        print("Klart (ingen notis skickad).")
