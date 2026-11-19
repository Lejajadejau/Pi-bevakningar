"""Emberlin – lediga bord för 4 via SevenRooms (bokningslänken på emberlinstockholm.com).

Larmar när ett bord går att boka direkt (inte bara skicka förfrågan om)
tisdag–torsdag med starttid 17.00–18.00, 15 mars–30 april 2027.
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
FORSTA = date(2027, 3, 15)
SISTA = date(2027, 4, 30)
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


def kontrollera(webblasare=None):
    idag = date.today()
    if idag > SISTA:
        return []
    data = {}
    start = max(FORSTA, idag)
    while start <= SISTA:
        dagar = min(7, (SISTA - start).days + 1)
        data.update(_hamta(start, dagar))
        start += timedelta(days=dagar)

    fynd = []
    for datum_str, pass_lista in data.items():
        d = date.fromisoformat(datum_str)
        if d.weekday() not in VECKODAGAR or not FORSTA <= d <= SISTA:
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
