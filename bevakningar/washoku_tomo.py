"""Washoku TOMO (Stockholm) – lediga bord för 4 personer.

Restaurangen bokar via BokaBord. Bevakningen öppnar bokningssidan i en
osynlig webbläsare, väljer Dinner och 4 gäster, öppnar vyn "Check
availability several days ahead" och läser de tider som sidan själv hämtar.
Den bokar aldrig något och fyller inte i några uppgifter.
"""

import datetime as dt
import re

NAMN = "Washoku Tomo – bord för 4"
INTERVALL_MINUTER = 30
AKTIV = True
BEHOVER_WEBBLASARE = True

ANTAL_GASTER = 4
BOKNINGSSIDA = (
    "https://app.bokabord.se/reservation/"
    "?app_type=bokabord&hash=b887b98cc3a7fa51c33286819138f8ee&lang=en"
)
RESTAURANG_URL = "https://www.washokutomo.se/"

VECKODAGAR = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]
MANADER = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]


def _klicka_text(sida, monster, timeout=15000):
    sida.get_by_text(re.compile(monster)).first.click(timeout=timeout)


def kontrollera(webblasare):
    kontext = webblasare.new_context(locale="en-GB", viewport={"width": 800, "height": 1300})
    sida = kontext.new_page()
    svar = []

    def fanga(response):
        if "getNextTimeList" in response.url and response.status == 200:
            try:
                svar.append(response.json())
            except Exception:
                pass

    sida.on("response", fanga)
    try:
        sida.goto(BOKNINGSSIDA, wait_until="networkidle", timeout=60000)
        _klicka_text(sida, r"^\s*Dinner\s*$")
        sida.locator(f"text=/^\\s*{ANTAL_GASTER}\\s*guests?\\s*$/").first.click(timeout=15000)
        sida.wait_for_timeout(1500)

        # Välj ett datum (i dag eller någon av de närmaste dagarna) för att
        # få fram länken till vyn med flera dagar.
        idag = dt.date.today()
        hittad = False
        for i in range(10):
            dag = idag + dt.timedelta(days=i)
            cell = sida.get_by_text(f"{dag.day:02d}", exact=True)
            if cell.count() == 0:
                continue
            try:
                cell.first.click(timeout=5000)
            except Exception:
                continue
            sida.wait_for_timeout(2000)
            lank = sida.get_by_text(re.compile(r"Check availability several days ahead", re.I))
            if lank.count() > 0:
                lank.first.click(timeout=10000)
                hittad = True
                break
            # Fel dag (t.ex. stängt) – gå tillbaka till kalendern via datumet i sammanfattningen.
            try:
                sida.get_by_text(re.compile(rf"^\s*{dag.day} \w+\s*$")).first.click(timeout=3000)
                sida.wait_for_timeout(1000)
            except Exception:
                pass
        if not hittad:
            raise RuntimeError("Hittade inte vyn för flera dagar på bokningssidan – sidan kan ha ändrats.")

        # Scrolla nedåt så att sidan hämtar även kommande månader.
        for _ in range(8):
            sida.mouse.wheel(0, 4000)
            sida.wait_for_timeout(1500)
        sida.wait_for_timeout(2000)
    finally:
        kontext.close()

    if not svar:
        raise RuntimeError("Bokningssidan skickade ingen tillgänglighet – sidan kan ha ändrats.")

    lediga = set()
    for data in svar:
        for datum, tider in (data.get("times") or {}).items():
            for tid in tider or []:
                lediga.add((datum, tid[0]))

    fynd = []
    for datum, tid in sorted(lediga):
        d = dt.date.fromisoformat(datum)
        if d < dt.date.today():
            continue
        fynd.append({
            "id": f"{datum} {tid}",
            "text": f"{VECKODAGAR[d.weekday()]} {d.day} {MANADER[d.month - 1]} kl. {tid}",
            "url": RESTAURANG_URL,
        })
    return fynd
