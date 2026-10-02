#!/usr/bin/env python3
"""Bevakningsmotor för Raspberry Pi.

Kör alla bevakningar i mappen bevakningar/ som är "på tur" och skickar
pushnotiser via ntfy när en bevakning hittar något nytt.

Varje bevakning är en Python-fil i bevakningar/ med:
    NAMN = "Läsbart namn"
    INTERVALL_MINUTER = 30           # hur ofta den ska köras
    AKTIV = True                     # sätt False för att pausa
    BEHOVER_WEBBLASARE = True/False  # om kontrollera() får en webbläsare
    def kontrollera(webblasare) -> list[dict]
        # varje fynd: {"id": unikt-id, "text": "kort text", "url": "länk"}

Motorn minns vilka fynd som redan rapporterats och meddelar bara nya.
Ett fynd som försvinner och sedan dyker upp igen rapporteras på nytt.
"""

import importlib.util
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROT = Path(__file__).resolve().parent
BEVAKNINGAR = ROT / "bevakningar"
KONFIG = ROT / "config.local.json"
TILLSTAND = ROT / "state" / "tillstand.json"
FEL_INTERVALL_S = 24 * 3600  # max ett felmeddelande per bevakning och dygn


def logga(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), text, flush=True)


def las_json(sokvag, standard):
    try:
        return json.loads(sokvag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return standard


def spara_tillstand(tillstand):
    if "--test" in sys.argv:
        return  # testkörningar påverkar inte vad som räknas som nytt
    TILLSTAND.parent.mkdir(exist_ok=True)
    tmp = TILLSTAND.with_suffix(".tmp")
    tmp.write_text(json.dumps(tillstand, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TILLSTAND)


def skicka_notis(konfig, titel, text, url=None, prioritet="default"):
    server = konfig.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    amne = konfig["ntfy_amne"]
    huvuden = {
        "Title": titel.encode("utf-8"),
        "Priority": prioritet,
        "Tags": "bell",
    }
    if url:
        huvuden["Click"] = url
    req = urllib.request.Request(
        f"{server}/{amne}", data=text.encode("utf-8"), headers=huvuden, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as svar:
        svar.read()


def ladda_bevakningar():
    moduler = []
    for fil in sorted(BEVAKNINGAR.glob("*.py")):
        if fil.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"bevakningar.{fil.stem}", fil)
            modul = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modul)
            modul.ID = fil.stem
            moduler.append(modul)
        except Exception:
            logga(f"Kunde inte ladda {fil.name}:\n{traceback.format_exc()}")
    return moduler


class Webblasare:
    """Startar Chromium först när en bevakning faktiskt behöver den."""

    def __init__(self, konfig):
        self.konfig = konfig
        self._pw = None
        self._browser = None

    def hamta(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            sokvag = self.konfig.get("chromium_sokvag")
            if not sokvag:
                for kandidat in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
                    if Path(kandidat).exists():
                        sokvag = kandidat
                        break
            self._browser = self._pw.chromium.launch(
                headless=True,
                executable_path=sokvag,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        return self._browser

    def stang(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()


def main():
    tvinga = "--alla" in sys.argv  # kör allt direkt, oavsett intervall
    test = "--test" in sys.argv    # skriv ut fynd, skicka inga notiser
    konfig = las_json(KONFIG, None)
    if not konfig or not konfig.get("ntfy_amne"):
        logga("config.local.json saknas eller saknar ntfy_amne – kör installera.sh först.")
        sys.exit(1)

    tillstand = las_json(TILLSTAND, {})
    nu = time.time()
    webblasare = Webblasare(konfig)
    try:
        for modul in ladda_bevakningar():
            namn = getattr(modul, "NAMN", modul.ID)
            if not getattr(modul, "AKTIV", True):
                continue
            post = tillstand.setdefault(modul.ID, {"senast_korning": 0, "sett": [], "senast_fel": 0})
            intervall = getattr(modul, "INTERVALL_MINUTER", 60) * 60
            if not tvinga and nu - post["senast_korning"] < intervall - 30:
                continue

            logga(f"Kör: {namn}")
            post["senast_korning"] = nu
            try:
                arg = webblasare.hamta() if getattr(modul, "BEHOVER_WEBBLASARE", False) else None
                fynd = modul.kontrollera(arg) or []
            except Exception as fel:
                logga(f"Fel i {namn}:\n{traceback.format_exc()}")
                if not test and nu - post.get("senast_fel", 0) > FEL_INTERVALL_S:
                    post["senast_fel"] = nu
                    try:
                        skicka_notis(konfig, f"⚠️ {namn}", f"Bevakningen fungerar inte just nu: {str(fel)[:300]}", prioritet="low")
                    except Exception:
                        logga("Kunde inte heller skicka felnotis.")
                spara_tillstand(tillstand)
                continue

            tidigare = set(post.get("sett", []))
            nya = [f for f in fynd if f["id"] not in tidigare]
            post["sett"] = [f["id"] for f in fynd]
            logga(f"  {len(fynd)} fynd, varav {len(nya)} nya")

            if nya:
                text = "\n".join(f["text"] for f in nya[:15])
                if len(nya) > 15:
                    text += f"\n… och {len(nya) - 15} till"
                if test:
                    print(text)
                else:
                    skicka_notis(konfig, namn, text, url=nya[0].get("url"), prioritet="high")
            spara_tillstand(tillstand)
    finally:
        webblasare.stang()
        if test:
            logga("Testkörning – inga notiser skickades.")


if __name__ == "__main__":
    main()
