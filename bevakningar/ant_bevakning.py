"""ANT-bevakning – Anthropic (Nasdaq: ANT) efter börsnoteringen 19 november 2026.

Larmar när
  * kursen går under introduktionspriset,
  * kursen ligger 30 % under högsta stängningskursen sedan noteringen,
  * kursen faller mer än 8 % på en dag,
och påminner dagen före större frisläpp av låsta aktier och före rapporter.

Kursdata hämtas från Yahoo Finance (gratis, inget konto). Fram till noteringen
returnerar bevakningen inga fynd och skickar inga felnotiser.
"""

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NAMN = "ANT-bevakning"
INTERVALL_MINUTER = 30
AKTIV = True
BEHOVER_WEBBLASARE = False

TICKER = "ANT"
NOTERINGSDAG = date(2026, 11, 19)

# Fylls i när priset är satt (intervallet är 70–78 dollar). None = larmet är avstängt.
INTRO_PRIS = None

FALL_FRAN_TOPP = 0.30   # 30 % under högsta stängningskurs
DAGSFALL = 0.08         # 8 % på en dag
ATERSTALL_MARGINAL = 0.03  # kursen måste upp 3 % över intropriset innan larmet kan gå igen

# Påminnelser skickas dagen före. Datumen är ungefärliga tills bolaget meddelat exakta datum.
PAMINNELSER = {
    date(2026, 12, 15): "Ca dag 27: övriga aktieägare börjar få sälja låsta aktier i steg.",
    date(2027, 2, 3): "Ca dag 76: upp till 1,6 miljarder aktier kan då ha blivit säljbara.",
    date(2027, 5, 18): "Ca dag 180: grundarna får sälja upp till 29 % av sina aktier.",
    # Rapportdatum läggs till när de är kända, t.ex.:
    # date(2027, 2, 25): "Helårsrapport 2026 – därefter blir ytterligare 35 % säljbara.",
}

NY = ZoneInfo("America/New_York")
TILLSTAND = Path(__file__).resolve().parent.parent / "state" / "ant_bevakning.json"
URL_KURS = "https://finance.yahoo.com/quote/ANT"


def _hamta(symbol, intervall="1d", period="1y"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={period}&interval={intervall}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as svar:
        return json.load(svar)["chart"]["result"][0]


def _las_tillstand():
    try:
        return json.loads(TILLSTAND.read_text())
    except Exception:
        return {}


def _spara_tillstand(t):
    TILLSTAND.parent.mkdir(parents=True, exist_ok=True)
    TILLSTAND.write_text(json.dumps(t, indent=2))


def _kronor(usd, kurs_sek):
    if not kurs_sek:
        return ""
    return f" (ca {usd * kurs_sek:,.0f} kr)".replace(",", " ")


def _paminnelser(idag):
    fynd = []
    for datum, text in PAMINNELSER.items():
        if datum - timedelta(days=1) == idag:
            fynd.append({
                "id": f"ant-paminnelse-{datum.isoformat()}",
                "text": f"Imorgon ({datum:%-d/%-m}): {text}",
                "url": URL_KURS,
            })
    return fynd


def kontrollera(webblasare=None):
    idag = datetime.now(ZoneInfo("Europe/Stockholm")).date()
    fynd = _paminnelser(idag)

    if idag < NOTERINGSDAG:
        return fynd

    try:
        data = _hamta(TICKER)
    except Exception:
        return fynd  # ingen handel ännu eller tillfälligt fel – försök igen nästa gång

    stamplar = data.get("timestamp") or []
    stang = (data.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    dagar = []
    for ts, c in zip(stamplar, stang):
        d = datetime.fromtimestamp(ts, timezone.utc).astimezone(NY).date()
        if c is not None and d >= NOTERINGSDAG:
            dagar.append((d, c))
    pris = data.get("meta", {}).get("regularMarketPrice")
    if not dagar or pris is None:
        return fynd

    try:
        sek = _hamta("SEK=X", period="5d").get("meta", {}).get("regularMarketPrice")
    except Exception:
        sek = None

    handelsdag = datetime.now(NY).date()
    tidigare = [c for d, c in dagar if d < handelsdag]
    topp = max(c for _, c in dagar)
    t = _las_tillstand()

    # 1. Under introduktionspriset
    if INTRO_PRIS:
        if pris < INTRO_PRIS and not t.get("under_intro"):
            t["under_intro"] = handelsdag.isoformat()
            fynd.append({
                "id": f"ant-under-intro-{handelsdag.isoformat()}",
                "text": (f"ANT {pris:.2f} $ – under intropriset {INTRO_PRIS:.2f} $"
                         f"{_kronor(pris, sek)}."),
                "url": URL_KURS,
            })
        elif pris > INTRO_PRIS * (1 + ATERSTALL_MARGINAL):
            t.pop("under_intro", None)

    # 2. 30 % under högsta stängningskurs (larmar en gång per topp)
    if pris <= topp * (1 - FALL_FRAN_TOPP):
        fynd.append({
            "id": f"ant-fran-topp-{topp:.2f}",
            "text": (f"ANT {pris:.2f} $ – {1 - pris / topp:.0%} under toppen {topp:.2f} $"
                     f"{_kronor(pris, sek)}."),
            "url": URL_KURS,
        })

    # 3. Stort dagsfall
    if tidigare:
        foregaende = tidigare[-1]
        forandring = pris / foregaende - 1
        if forandring <= -DAGSFALL:
            fynd.append({
                "id": f"ant-dagsfall-{handelsdag.isoformat()}",
                "text": (f"ANT ner {abs(forandring):.0%} idag till {pris:.2f} $"
                         f"{_kronor(pris, sek)}."),
                "url": URL_KURS,
            })

    _spara_tillstand(t)
    return fynd


if __name__ == "__main__":
    for f in kontrollera():
        print(f["text"])
    print("Klart.")
