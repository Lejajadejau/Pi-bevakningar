"""Allez ! – felrapporter från franska-appen.

Appen sparar anonyma felrapporter i sin databas (Supabase). En gång per dygn
hämtas en sammanställning av den senaste veckans fel. Notis kommer bara när en
ny sorts fel dyker upp – samma fel ger inte notis varje dag.

Sammanställningen innehåller bara felmeddelande, var i appen felet uppstod,
appversion och antal – inga uppgifter om vem det gällde.
"""

import hashlib
import json
import urllib.request

NAMN = "Allez – felrapporter"
INTERVALL_MINUTER = 24 * 60
AKTIV = True
BEHOVER_WEBBLASARE = False

SUPABASE = "https://yspfxxfaokpivyvyvojm.supabase.co/rest/v1/rpc/error_digest"
# Appens publika nyckel (den finns redan öppet i appens kod och ger bara det appen själv får göra)
PUBLIK_NYCKEL = "sb_publishable_r19H1zGgHZAelngaLqxBSQ_71WGacRA"
APPEN = "https://allez-franska.pages.dev"


def hamta():
    req = urllib.request.Request(
        SUPABASE,
        data=b"{}",
        headers={
            "apikey": PUBLIK_NYCKEL,
            "Authorization": "Bearer " + PUBLIK_NYCKEL,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as svar:
        return json.loads(svar.read().decode("utf-8"))


def kontrollera(webblasare):
    fynd = []
    for rad in hamta():
        msg = (rad.get("msg") or "").strip()
        plats = rad.get("place") or "okänt ställe"
        nyckel = hashlib.sha1(f"{msg}|{plats}".encode("utf-8")).hexdigest()[:12]
        antal = rad.get("antal") or 1
        fynd.append({
            "id": f"allez-{nyckel}",
            "text": (
                f"Fel i Allez: {msg[:160]}\n"
                f"{antal} {'gång' if antal == 1 else 'gånger'} senaste veckan · {plats} · version {rad.get('ver') or '?'}\n"
                "Be Claude rätta felet i Allez."
            ),
            "url": APPEN,
        })
    return fynd


if __name__ == "__main__":
    for f in kontrollera(None):
        print(f["text"], "\n")
    print("Klart.")
