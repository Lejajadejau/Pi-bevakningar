"""Helgtips för Stockholm – varje lördag cirka kl. 09.

Claude (Anthropics API, med webbsökning) letar fram vad som faktiskt händer i
Stockholm den här helgen och skriver 6–8 tips i stil med DN På Stan, anpassade
efter SMHI:s prognos. Tipsen kommer som en ntfy-notis och sparas även i
state/helgtips/.

Kräver claude_nyckel i config.local.json (samma som HD-bevakningen).
Valfritt: helgtips_modell (standard claude-sonnet-5-5).

Fristående test (skriver ut tipsen för kommande helg, skickar ingen notis):
  ./venv/bin/python bevakningar/helgtips.py --testa
"""

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

NAMN = "Helgtips"
INTERVALL_MINUTER = 5
AKTIV = True
BEHOVER_WEBBLASARE = False
PRIORITET = "default"

START_KL = dt.time(8, 50)   # generering tar ett par minuter → notisen kommer runt 09.00
SLUT_KL = dt.time(13, 0)    # efter detta ger vi upp för den här helgen
MAX_FORSOK = 3
MODELL = "claude-sonnet-5-5"

STOCKHOLM = ZoneInfo("Europe/Stockholm")
ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
TILLSTAND = ROT / "state" / "helgtips.json"
ARKIV = ROT / "state" / "helgtips"

DAGAR = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MANADER = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
           "september", "oktober", "november", "december"]

INSTRUKTION = """Du är kulturredaktör på en Stockholmstidning och skriver helgtips i stil med DN På Stan.
Läsaren är en urban, medelålders, välutbildad familj i Stockholm. Tipsen kommer som en
mobilnotis lördag morgon kl. 9.

Helgen gäller {lordag} och {sondag}.

Använd webbsökningen för att ta reda på vad som faktiskt pågår i Stockholm just den här helgen:
teater och opera, konserter (klassiskt, jazz, pop), utställningar (gärna nya eller "sista chansen"),
film och Cinemateket, föredrag och samtal, marknader, mat och utflykter i staden eller nära.
Kolla också SMHI:s prognos för Stockholm i helgen och anpassa: inomhus om det regnar,
gärna något utomhus om det blir fint.

Välj 6–8 tips. Blanda: minst ett som passar hela familjen, gärna något gratis och gärna något
som är sista chansen. Undvik det självklara turistiga om det inte händer något särskilt där.
Kontrollera datum och tider noga – tipsa bara om sådant som verkligen går den här helgen.
Hitta inte på något. Hittar du inte säkra uppgifter om en sak, ta inte med den.

Skriv svaret mellan markeringarna <<<HELGTIPS>>> och <<<SLUT>>>, som ren text utan markdown:
- Första raden: vädret i helgen i en mening.
- Sedan en tom rad och därefter ett tips per stycke, så här:
  • Titel – plats. Dag och tid. En eller två meningar om varför det är värt det. Pris om det är känt.
  https://länk-till-källan
- Högst cirka 2 500 tecken totalt.
"""


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
    TILLSTAND.parent.mkdir(exist_ok=True)
    tmp = TILLSTAND.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TILLSTAND)


def _datumtext(d):
    return f"{DAGAR[d.weekday()]} {d.day} {MANADER[d.month - 1]}"


def _claude(konfig, prompt):
    modell = konfig.get("helgtips_modell") or MODELL
    huvuden = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
               "Authorization": "Bearer " + konfig["claude_nyckel"].strip()}
    if konfig.get("claude_arbetsyta"):
        huvuden["anthropic-workspace-id"] = konfig["claude_arbetsyta"].strip()
    meddelanden = [{"role": "user", "content": prompt}]
    texter = []
    for _ in range(4):  # "pause_turn" betyder att Claude vill fortsätta söka
        kropp = json.dumps({
            "model": modell,
            "max_tokens": 8000,
            "messages": meddelanden,
            "tools": [{
                "type": "web_search_20260318", "name": "web_search", "max_uses": 15,
                "user_location": {"type": "approximate", "city": "Stockholm",
                                  "country": "SE", "timezone": "Europe/Stockholm"},
            }],
        }).encode("utf-8")
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                     data=kropp, method="POST", headers=huvuden)
        try:
            with urllib.request.urlopen(req, timeout=600) as svar:
                data = json.loads(svar.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("Claude-nyckeln godtas inte längre – den har troligen gått ut.")
            raise RuntimeError(f"Claude svarade {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        innehall = data.get("content", [])
        texter += [b.get("text", "") for b in innehall if b.get("type") == "text"]
        if data.get("stop_reason") != "pause_turn":
            break
        meddelanden = meddelanden + [{"role": "assistant", "content": innehall}]

    text = "".join(texter)
    if "<<<HELGTIPS>>>" not in text:
        raise RuntimeError(f"svaret saknade helgtips: {text[-300:]}")
    tips = text.split("<<<HELGTIPS>>>", 1)[1].split("<<<SLUT>>>", 1)[0].strip()
    # ntfy gör meddelanden över 4 096 byte till bilagor – håll oss under.
    while len(tips.encode("utf-8")) > 3900:
        tips = tips.rsplit("\n\n", 1)[0]
    return tips


def skapa_helgtips(konfig, lordag):
    prompt = INSTRUKTION.format(lordag=_datumtext(lordag),
                                sondag=_datumtext(lordag + dt.timedelta(days=1)))
    return _claude(konfig, prompt)


def kontrollera(webblasare):
    konfig = _konfig()
    if not konfig.get("claude_nyckel"):
        return []
    nu = dt.datetime.now(STOCKHOLM)
    if nu.weekday() != 5 or not (START_KL <= nu.time() < SLUT_KL):
        return []

    vecka = nu.strftime("%G-V%V")
    t = _las_tillstand()
    post = t.setdefault(vecka, {"forsok": 0, "klar": False})
    if post["klar"] or post["forsok"] >= MAX_FORSOK:
        return []

    post["forsok"] += 1
    _spara_tillstand(t)  # räknas även om det går fel, så vi inte försöker i det oändliga
    tips = skapa_helgtips(konfig, nu.date())

    ARKIV.mkdir(parents=True, exist_ok=True)
    (ARKIV / f"{vecka}.txt").write_text(tips, encoding="utf-8")
    post["klar"] = True
    _spara_tillstand(t)
    return [{"id": f"helgtips-{vecka}", "text": tips, "titel": f"Helgtips {nu.day} {MANADER[nu.month - 1]}"}]


if __name__ == "__main__":
    if "--testa" in sys.argv:
        konfig = _konfig()
        if not konfig.get("claude_nyckel"):
            sys.exit("claude_nyckel saknas i config.local.json.")
        idag = dt.datetime.now(STOCKHOLM).date()
        lordag = idag + dt.timedelta(days=(5 - idag.weekday()) % 7)
        print(f"Tar fram helgtips för {_datumtext(lordag)} … (tar ett par minuter)\n")
        start = time.time()
        print(skapa_helgtips(konfig, lordag))
        print(f"\n[{int(time.time() - start)} s]")
