"""Emberlin – lediga bord för 4 via SevenRooms (bokningslänken på emberlinstockholm.com).

Larmar när ett bord går att boka direkt (inte bara skicka förfrågan om)
tisdag–torsdag med starttid 17.00–18.00 under perioderna i PERIODER
(19 januari–11 februari och 9 mars–30 april 2027).
"""

import json
import urllib.request
from datetime import date, timedelta

NAMN = "Emberlin – lediga bord"
INTERVALL_MINUTER = 30
AKTIV = True
BEHOVER_WEBBLASARE = False

VENUE = "emberlin"
ANTAL = 4
PERIODER = [
    (date(2027, 1, 19), date(2027, 2, 11)),   # slutet av januari – början av februari
    (date(2027, 3, 9), date(2027, 4, 30)),
]
VECKODAGAR = {1, 2, 3}          # tisdag, onsdag, torsdag (måndag = 0)
TIDIGAST, SENAST = "17:00", "18:00"
BOKA_URL = "https://www.emberlinstockholm.com/"

DAGNAMN = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]


def _hamta(start, dagar):
    url = ("https://www.sevenrooms.com/api-yoa/availability/widget/range"
           f"?venue={VENUE}&time_slot=17:30&party_size={ANTAL}&halo_size_interval=8"
           f"&start_date={start:%m-%d-%Y}&num_days={dagar}&channel=SEVENROOMS_WIDGET")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sevenrooms.com/reservations/emberlin",
    })
    with urllib.request.urlopen(req, timeout=20) as svar:
        return json.load(svar).get("data", {}).get("availability", {}) or {}


def _i_perioderna(d):
    return any(forsta <= d <= sista for forsta, sista in PERIODER)


def kontrollera(webblasare=None):
    idag = date.today()
    data = {}
    # SevenRooms godtar bara num_days = 1 eller 3. Vi frågar om tisdag–torsdag
    # i varje vecka (3 dagar från tisdagen), alltså en fråga per vecka.
    for forsta, sista in PERIODER:
        tisdag = forsta - timedelta(days=(forsta.weekday() - 1) % 7)
        while tisdag <= sista:
            if tisdag + timedelta(days=2) >= idag:
                data.update(_hamta(max(tisdag, idag), 3))
            tisdag += timedelta(days=7)

    fynd = []
    for datum_str, pass_lista in data.items():
        d = date.fromisoformat(datum_str)
        if d.weekday() not in VECKODAGAR or not _i_perioderna(d):
            continue
        tider = sorted({
            t.get("time")
            for p in pass_lista
            for t in p.get("times", [])
            if t.get("type") == "book" and TIDIGAST <= (t.get("time") or "") <= SENAST
        })
        for tid in tider:
            fynd.append({
                "id": f"emberlin-{datum_str}-{tid}",
                "text": f"Emberlin: bord för {ANTAL} {DAGNAMN[d.weekday()]} {d.day}/{d.month} kl. {tid}.",
                "url": BOKA_URL,
            })
    return fynd


if __name__ == "__main__":
    for f in kontrollera():
        print(f["text"])
    print("Klart.")
