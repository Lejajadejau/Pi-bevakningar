"""Corniche – La Grande Corniche, stål med Bleu Marine-urtavla, åter i lager.

Butiken kör WooCommerce. Bevakningen läser butikens öppna Store API för
produkten (id 89305). Där listas bara de varianter som går att köpa; i
november 2026 fanns Azur Bleu och Salmon men inte Bleu Marine (slutsåld).
Notis kommer när en variant med "Marine" i namnet dyker upp och är i lager.
Går den ur lager och sedan kommer tillbaka blir det en ny notis.
"""

import json
import urllib.request

NAMN = "Corniche – Bleu Marine i lager"
INTERVALL_MINUTER = 30
AKTIV = True
BEHOVER_WEBBLASARE = False
PRIORITET = "high"

PRODUKT_ID = 89305
API = "https://cornichewatches.com/wp-json/wc/store/v1/products/"
PRODUKTSIDA = "https://cornichewatches.com/shop/la-grande-corniche/"
SOKORD = "marine"


def hamta(produkt_id):
    req = urllib.request.Request(
        f"{API}{produkt_id}",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) pi-bevakningar",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as svar:
        return json.loads(svar.read().decode("utf-8"))


def _pris(data):
    p = data.get("prices") or {}
    try:
        belopp = int(p["price"]) / 10 ** int(p.get("currency_minor_unit", 2))
        return f"{belopp:,.0f} {p.get('currency_code', '')}".replace(",", " ").strip()
    except Exception:
        return ""


def kontrollera(webblasare):
    produkt = hamta(PRODUKT_ID)
    if "variations" not in produkt:
        raise RuntimeError("Butikens API svarade utan varianter – sidan kan ha ändrats.")

    fynd = []
    for variant in produkt["variations"]:
        namn = " ".join(a.get("value", "") for a in variant.get("attributes", []))
        if SOKORD not in namn.lower():
            continue
        detalj = hamta(variant["id"])
        if not (detalj.get("is_in_stock") and detalj.get("is_purchasable", True)):
            continue
        pris = _pris(detalj)
        fynd.append({
            "id": f"corniche-{variant['id']}",
            "titel": "⌚ Bleu Marine finns i lager!",
            "text": f"{namn}{' – ' + pris if pris else ''} går att köpa nu hos Corniche.",
            "url": PRODUKTSIDA,
        })
    return fynd


if __name__ == "__main__":
    for f in kontrollera(None):
        print(f["text"])
    print("Klart.")
