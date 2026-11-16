"""Fitouflyg – billiga flyg till och från huset i Fitou.

Bevakar flyg mellan Arlanda/Skavsta och de fyra flygplatserna runt Fitou
(Barcelona, Perpignan, Montpellier, Béziers). Det finns inget pristak. Bevakningen
säger i stället till när priset är relativt sett lågt, antingen enligt Google
Flights egen bedömning (låg/normal/hög nivå och normalt prisspann) eller jämfört
med vad bevakningen själv har sett för samma resa tidigare.

Två delar:

1. Långhelger och lov – tur och retur, första till sista lediga dagen.
   Långhelgerna räknas ut automatiskt ur de svenska helgdagarna och klämdagarna
   (påsk, Kristi himmelfärd, 1 maj, nationaldagen, trettondagen). Infaller
   nationaldagen på en helg söks både fredag–söndag och lördag–måndag.
   Loven (jul-, sport-, påsk- och höstlov) och skolavslutningen följer
   Stockholms stads lovdatum. Pi:n läser stadens sida en gång i månaden och
   låter Claude plocka ut datumen (sparas i state/lov_stockholm.json), så nya
   läsår kommer med av sig själva. data/lov_stockholm.json är grunden om
   hämtningen inte fungerar.
   Vanliga helger söks inte. Notis kommer direkt när en långhelg är billig,
   och på nytt om priset sjunker ytterligare.

2. Sommaren – från dagen efter skolavslutningen till 15 augusti.
   Enkelresor per person, ner och hem var för sig (var tredje dag), så att
   resultatet passar oavsett vem som reser. En sammanfattning skickas varje
   söndag med de billigaste dagarna åt båda hållen.

Priserna gäller en vuxen. Alla priser är Googles uppgifter vid sökningen.

SerpApi-kontot delas med Flygfynd. Fitouflyg använder högst ett visst antal
sökningar i månaden (standard 70) och lämnar alltid minst 40 kvar på kontot,
så att Flygfynd inte blir utan.

Inställningar i config.local.json:
  "serpapi_nyckel": "..."               (krävs, samma som Flygfynd)
  "fitou_max_per_manad": 70              (valfritt)

Kommandon:
  ./venv/bin/python bevakningar/fitouflyg.py --plan    visar vilka resor som bevakas (inga sökningar)
  ./venv/bin/python bevakningar/fitouflyg.py --testa   provsöker en långhelg och en sommardag (ingen notis)
  ./venv/bin/python bevakningar/fitouflyg.py --lage    visar senaste priserna som bevakningen sett
  ./venv/bin/python bevakningar/fitouflyg.py --lov     hämtar stadens lovdatum nu och visar dem (sparar inget)
"""

import datetime as dt
import json
import statistics
import sys
import time
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

NAMN = "Fitouflyg"
INTERVALL_MINUTER = 24 * 60
AKTIV = True
BEHOVER_WEBBLASARE = False
PRIORITET = "default"

HEMMA = "ARN,NYO"
FITOU = "BCN,PGF,MPL,BZR"
FLYGPLATSER = {"ARN": "Arlanda", "NYO": "Skavsta", "BCN": "Barcelona", "PGF": "Perpignan",
               "MPL": "Montpellier", "BZR": "Béziers"}
MAX_RESTID_MIN = 12 * 60        # hoppa över resor med orimligt långa byten
HORISONT_DAGAR = 320            # Google Flights visar knappt ett år framåt
MINST_DAGAR_KVAR = 3
SOMMAR_STEG_DAGAR = 3           # var tredje dag under sommaren
SOMMAR_SLUT = (8, 15)           # 15 augusti
SOKNINGAR_PER_KORNING = 3
STANDARD_MAX_PER_MANAD = 70
RESERV_PA_KONTOT = 40           # Flygfynd har företräde till de sista sökningarna
BILLIGT_MOT_EGEN_MEDIAN = 0.85  # 15 % under vad bevakningen brukar se
HISTORIK_LANGD = 40

STOCKHOLM = ZoneInfo("Europe/Stockholm")
ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
LOVFIL = ROT / "data" / "lov_stockholm.json"
TILLSTAND = ROT / "state" / "fitouflyg.json"
HAMTADE_LOV = ROT / "state" / "lov_stockholm.json"
LOVSIDA = "https://grundskola.stockholm/en/school-terms-and-vacation-periods-for-compulsory-school/"
LOV_UPPDATERING_DAGAR = 30
CLAUDE = "https://api.anthropic.com/v1/messages"
LOV_MODELL = "claude-sonnet-5-5"
API = "https://serpapi.com/search.json"
KONTO_API = "https://serpapi.com/account.json"

MANADER = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]
VECKODAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]


# ---------------------------------------------------------------- hjälpfunktioner

def _las_json(sokvag, standard):
    try:
        return json.loads(sokvag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return standard


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
        konto = _hamta(KONTO_API, {"api_key": self.nyckel})
        return konto.get("total_searches_left", konto.get("plan_searches_left", 0))

    def sok(self, **p):
        self.antal += 1
        p.update({"api_key": self.nyckel, "currency": "SEK", "gl": "se", "hl": "sv"})
        try:
            return _hamta(API, p)
        except RuntimeError as fel:
            if "hasn't returned any results" in str(fel):
                return {}
            raise


def _tal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        siffror = "".join(c for c in v if c.isdigit())
        return float(siffror) if siffror else None
    return None


def _kr(belopp):
    return f"{round(belopp):,} kr".replace(",", " ")


def _versal(text):
    return text[:1].upper() + text[1:]


def _dag(d):
    return f"{VECKODAGAR[d.weekday()]} {d.day} {MANADER[d.month - 1]}"


def _period(fran, till):
    if fran.month == till.month:
        return f"{VECKODAGAR[fran.weekday()]} {fran.day}–{VECKODAGAR[till.weekday()]} {till.day} {MANADER[till.month - 1]}"
    return f"{_dag(fran)}–{_dag(till)}"


# ---------------------------------------------------------------- kalendern

def _pask(ar):
    """Påskdagen (gregorianska kalendern)."""
    a, b, c = ar % 19, ar // 100, ar % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    manad = (h + l - 7 * m + 114) // 31
    dag = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(ar, manad, dag)


def _ledigt_runt(dag, fri_helg=False):
    """Lediga perioder runt en helgdag, med klämdag där det passar."""
    v = dag.weekday()
    d = dt.timedelta
    if v == 0:
        return [(dag - d(2), dag)]
    if v == 1:
        return [(dag - d(3), dag)]          # klämdag måndag
    if v == 3:
        return [(dag, dag + d(3))]          # klämdag fredag
    if v == 4:
        return [(dag, dag + d(2))]
    if fri_helg and v == 5:
        return [(dag - d(1), dag + d(1)), (dag, dag + d(2))]
    if fri_helg and v == 6:
        return [(dag - d(2), dag), (dag - d(1), dag + d(1))]
    return []


def _langhelger(ar):
    pask = _pask(ar)
    d = dt.timedelta
    perioder = [("Påsk", pask - d(2), pask + d(1))]
    for namn, dag, fri in (("Kristi himmelfärd", pask + d(39), False),
                           ("Första maj", dt.date(ar, 5, 1), False),
                           ("Nationaldagen", dt.date(ar, 6, 6), True),
                           ("Trettondagen", dt.date(ar, 1, 6), False)):
        for fran, till in _ledigt_runt(dag, fri):
            perioder.append((namn, fran, till))
    return perioder


def _vecka(ar, vecka):
    """Lördagen före och söndagen efter en lovvecka."""
    mandag = dt.date.fromisocalendar(ar, vecka, 1)
    return mandag - dt.timedelta(days=2), mandag + dt.timedelta(days=6)


def _lovdata():
    """Grundfilen i arkivet, kompletterad och ersatt av det Pi:n själv har hämtat."""
    lov, sommar = {}, {}
    for kalla in (_las_json(LOVFIL, {}), _las_json(HAMTADE_LOV, {})):
        for l in kalla.get("lov", []):
            lov[(l["namn"], dt.date.fromisoformat(l["fran"]).year, dt.date.fromisoformat(l["fran"]).month)] = l
        for s in kalla.get("sommarlov", []):
            sommar[s["ar"]] = s
    return {"lov": list(lov.values()), "sommarlov": list(sommar.values())}


LOV_INSTRUKTION = """Nedan är texten från Stockholms stads sida om terminer och lov i grundskolan.
Dagens datum är {idag}. Plocka ut alla lov och skolavslutningar som står där.

Ange varje lov som en sammanhängande ledig period för eleverna, från första till sista
lediga dagen, med angränsande lördagar, söndagar och helgdagar inräknade. Exempel: ett
sportlov måndag–fredag vecka 9 blir lördagen före till söndagen efter. Jullovet är dagen
efter höstterminens sista dag till dagen före vårterminens första dag. Påsklovet tar med
långfredagen till annandag påsk om de ligger i anslutning. Ta inte med enstaka lovdagar
(t.ex. klämdagen efter Kristi himmelfärd) – de räknas ut på annat sätt.

Svara ENBART med JSON i exakt denna form, utan annan text:
{{"lov": [{{"namn": "Jullov|Sportlov|Påsklov|Höstlov", "fran": "ÅÅÅÅ-MM-DD", "till": "ÅÅÅÅ-MM-DD"}}],
  "sommarlov": [{{"ar": ÅÅÅÅ, "skolavslutning": "ÅÅÅÅ-MM-DD"}}]}}

Sidans text:
{text}"""


def _uppdatera_lov(konfig, idag, tvinga=False):
    """Hämtar stadens lovdatum högst en gång per månad. Fel stoppar inte bevakningen."""
    if not konfig.get("claude_nyckel"):
        return "claude_nyckel saknas – använder lovdatumen i arkivet"
    sparat = _las_json(HAMTADE_LOV, {})
    if not tvinga and time.time() - sparat.get("hamtat", 0) < LOV_UPPDATERING_DAGAR * 86400:
        return None
    try:
        req = urllib.request.Request(LOVSIDA, headers={"User-Agent": "Mozilla/5.0 pi-bevakningar"})
        with urllib.request.urlopen(req, timeout=60) as svar:
            html = svar.read().decode("utf-8", "replace")
        html = re.sub(r"(?is)<(script|style|nav|footer|header)\b.*?</\1>", " ", html)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()[:30000]

        huvuden = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                   "Authorization": "Bearer " + konfig["claude_nyckel"].strip()}
        if konfig.get("claude_arbetsyta"):
            huvuden["anthropic-workspace-id"] = konfig["claude_arbetsyta"].strip()
        kropp = json.dumps({"model": konfig.get("fitou_lov_modell") or LOV_MODELL, "max_tokens": 2000,
                            "messages": [{"role": "user", "content": LOV_INSTRUKTION.format(
                                idag=idag.isoformat(), text=text)}]}).encode("utf-8")
        req = urllib.request.Request(CLAUDE, data=kropp, method="POST", headers=huvuden)
        with urllib.request.urlopen(req, timeout=180) as svar:
            svarstext = "".join(b.get("text", "") for b in json.loads(svar.read().decode("utf-8"))["content"]
                                if b.get("type") == "text")
        data = json.loads(svarstext[svarstext.index("{"): svarstext.rindex("}") + 1])

        lov = []
        for l in data.get("lov", []):
            fran, till = dt.date.fromisoformat(l["fran"]), dt.date.fromisoformat(l["till"])
            if l.get("namn") in ("Jullov", "Sportlov", "Påsklov", "Höstlov") and 0 <= (till - fran).days <= 35:
                lov.append({"namn": l["namn"], "fran": fran.isoformat(), "till": till.isoformat()})
        sommar = []
        for s in data.get("sommarlov", []):
            avsl = dt.date.fromisoformat(s["skolavslutning"])
            if avsl.month == 6 and avsl.year == int(s["ar"]):
                sommar.append({"ar": avsl.year, "skolavslutning": avsl.isoformat()})
        if not lov and not sommar:
            raise ValueError("inga lovdatum i svaret")
    except Exception as fel:
        sparat["hamtat"] = time.time() - (LOV_UPPDATERING_DAGAR - 3) * 86400  # försök igen om tre dagar
        if "--test" not in sys.argv:
            HAMTADE_LOV.parent.mkdir(exist_ok=True)
            HAMTADE_LOV.write_text(json.dumps(sparat, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"kunde inte uppdatera lovdatumen: {fel}"

    # Behåll tidigare hämtade läsår som inte längre står på sidan.
    nya_nycklar = {(l["namn"], l["fran"][:7]) for l in lov}
    lov += [l for l in sparat.get("lov", []) if (l["namn"], l["fran"][:7]) not in nya_nycklar]
    sommar += [s for s in sparat.get("sommarlov", []) if s["ar"] not in {x["ar"] for x in sommar}]
    nytt = {"hamtat": time.time(), "kalla": LOVSIDA, "lov": sorted(lov, key=lambda l: l["fran"]),
            "sommarlov": sorted(sommar, key=lambda s: s["ar"])}
    if "--test" not in sys.argv:
        HAMTADE_LOV.parent.mkdir(exist_ok=True)
        HAMTADE_LOV.write_text(json.dumps(nytt, ensure_ascii=False, indent=2), encoding="utf-8")
    return nytt


def _lov_och_sommar(ar_lista):
    data = _lovdata()
    lov = [(l["namn"], dt.date.fromisoformat(l["fran"]), dt.date.fromisoformat(l["till"]))
           for l in data.get("lov", [])]
    kanda = {(n, f.year) for n, f, _ in lov}
    for ar in ar_lista:  # reservregler när läsåret inte finns i filen
        if ("Sportlov", ar) not in kanda:
            lov.append(("Sportlov", *_vecka(ar, 9)))
        if ("Höstlov", ar) not in kanda:
            lov.append(("Höstlov", *_vecka(ar, 44)))
    avslutning = {s["ar"]: dt.date.fromisoformat(s["skolavslutning"]) for s in data.get("sommarlov", [])}
    sommar = {}
    for ar in ar_lista:
        start = avslutning.get(ar, dt.date(ar, 6, 11)) + dt.timedelta(days=1)
        sommar[ar] = (start, dt.date(ar, *SOMMAR_SLUT))
    return lov, sommar


def _overlappar(a_fran, a_till, b_fran, b_till):
    return a_fran <= b_till and b_fran <= a_till


def planera(idag):
    """Alla resor som ska bevakas just nu: lista av dictar."""
    forsta = idag + dt.timedelta(days=MINST_DAGAR_KVAR)
    sista = idag + dt.timedelta(days=HORISONT_DAGAR)
    ar_lista = list(range(idag.year, sista.year + 1))
    lov, sommar = _lov_och_sommar(ar_lista)

    upptaget = [(f, t) for _, f, t in lov] + list(sommar.values())
    perioder = [(n, f, t) for n, f, t in lov]
    for ar in ar_lista:
        for n, f, t in _langhelger(ar):
            if not any(_overlappar(f, t, uf, ut) for uf, ut in upptaget):
                perioder.append((n, f, t))

    resor = []
    for namn, fran, till in sorted(perioder, key=lambda p: p[1]):
        if forsta <= fran <= sista:
            resor.append({"nyckel": f"helg:{fran}:{till}", "typ": "helg", "namn": namn,
                          "fran": fran, "till": till})
    for ar, (start, slut) in sommar.items():
        dag = start
        while dag <= slut:
            if forsta <= dag <= sista:
                for riktning in ("ner", "hem"):
                    resor.append({"nyckel": f"{riktning}:{dag}", "typ": riktning, "namn": f"Sommaren {ar}",
                                  "fran": dag, "till": dag, "ar": ar})
            dag += dt.timedelta(days=SOMMAR_STEG_DAGAR)
    return resor


def _intervall_dagar(resa, idag):
    kvar = (resa["fran"] - idag).days
    if resa["typ"] == "helg":
        return 5 if kvar < 45 else 10 if kvar < 120 else 20
    return 10 if kvar < 120 else 25


# ---------------------------------------------------------------- sökning

def _sok(sokare, resa):
    p = dict(engine="google_flights", adults=1, max_duration=MAX_RESTID_MIN,
             outbound_date=resa["fran"].isoformat())
    if resa["typ"] == "helg":
        p.update(type=1, departure_id=HEMMA, arrival_id=FITOU, return_date=resa["till"].isoformat())
    elif resa["typ"] == "ner":
        p.update(type=2, departure_id=HEMMA, arrival_id=FITOU)
    else:
        p.update(type=2, departure_id=FITOU, arrival_id=HEMMA)
    return tolka(sokare.sok(**p), resa["typ"])


def tolka(data, typ):
    """Plockar ut billigaste resan per Fitou-flygplats och Googles prisbedömning."""
    per = {}
    for alt in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        ben = alt.get("flights") or []
        pris = _tal(alt.get("price"))
        if not ben or not pris:
            continue
        hemma = ben[0]["departure_airport"].get("id") if typ != "hem" else ben[-1]["arrival_airport"].get("id")
        dit = ben[-1]["arrival_airport"].get("id") if typ != "hem" else ben[0]["departure_airport"].get("id")
        if dit not in FLYGPLATSER or (dit in per and per[dit]["pris"] <= pris):
            continue
        avgang = (ben[0]["departure_airport"].get("time") or " ?").split(" ")[-1]
        bolag = ", ".join(dict.fromkeys(b.get("airline", "") for b in ben if b.get("airline")))
        byten = len(ben) - 1
        per[dit] = {"pris": pris, "hemma": hemma, "avgang": avgang, "bolag": bolag,
                    "byten": byten}
    if not per:
        return None
    basta = min(per, key=lambda k: per[k]["pris"])
    insikt = data.get("price_insights") or {}
    spann = insikt.get("typical_price_range")
    return {
        "pris": per[basta]["pris"],
        "flygplats": basta,
        "per": {k: v["pris"] for k, v in per.items()},
        "detalj": per[basta],
        "niva": insikt.get("price_level"),
        "vanligt": spann if isinstance(spann, list) and len(spann) == 2 else None,
        "url": (data.get("search_metadata") or {}).get("google_flights_url")
               or "https://www.google.com/travel/flights",
    }


def bedom(res, tidigare_priser):
    """Skäl att kalla priset relativt lågt (tom lista = inte särskilt billigt)."""
    skal = []
    if res.get("niva") == "low":
        skal.append("lågt enligt Google")
    elif res.get("vanligt") and res["pris"] < res["vanligt"][0]:
        skal.append("under Googles normalspann")
    if len(tidigare_priser) >= 3:
        median = statistics.median(tidigare_priser)
        if res["pris"] <= BILLIGT_MOT_EGEN_MEDIAN * median:
            skal.append(f"{round(100 * (1 - res['pris'] / median))} % under vad bevakningen brukar se")
    return skal


# ---------------------------------------------------------------- texter

def _resetext(res, typ):
    det = res["detalj"]
    hemma = FLYGPLATSER.get(det["hemma"], det["hemma"] or "")
    plats = FLYGPLATSER[res["flygplats"]]
    vag = f"{hemma}–{plats}" if typ != "hem" else f"{plats}–{hemma}"
    byte = "direkt" if det["byten"] == 0 else f"{det['byten']} byte"
    return f"{vag} {det['avgang']}, {det['bolag']}, {byte}"


def _helgnotis(resa, res, skal):
    fran, till = resa["fran"], resa["till"]
    ovriga = ", ".join(f"{FLYGPLATSER[k]} {_kr(v)}" for k, v in sorted(res["per"].items(), key=lambda x: x[1])
                       if k != res["flygplats"])
    text = (f"{resa['namn']} {_period(fran, till)}: {_kr(res['pris'])} per person tur och retur "
            f"(ca {_kr(4 * res['pris'])} för fyra). {_resetext(res, 'helg')}. "
            f"{_versal('; '.join(skal))}")
    if res.get("vanligt"):
        text += f" (normalt {_kr(res['vanligt'][0])}–{_kr(res['vanligt'][1])})"
    text += "."
    if ovriga:
        text += f" Övriga: {ovriga}."
    return {
        "id": f"{resa['nyckel']}:{int(res['pris'] // 200)}",
        "titel": f"✈️ Fitou, {resa['namn']}: {_kr(res['pris'])}/person",
        "text": text,
        "url": res["url"],
    }


def _sommarsammanfattning(resor, uppgifter, idag):
    farska = time.time() - 35 * 86400
    rader = {"ner": [], "hem": []}
    ar = None
    for resa in resor:
        if resa["typ"] not in rader:
            continue
        post = uppgifter.get(resa["nyckel"]) or {}
        res = post.get("resultat")
        if not res or post.get("senast", 0) < farska:
            continue
        ar = resa["ar"]
        rader[resa["typ"]].append((res["pris"], resa, res, post.get("skal") or []))
    if not rader["ner"] and not rader["hem"]:
        return None

    stycken, basta_url = [], None
    for typ, rubrik in (("ner", "Ner"), ("hem", "Hem")):
        lista = sorted(rader[typ], key=lambda r: r[0])[:5]
        if not lista:
            continue
        if typ == "ner":
            basta_url = lista[0][2]["url"]
        stycken.append(f"{rubrik}:")
        for pris, resa, res, skal in lista:
            markering = f" ↓ {skal[0]}" if skal else ""
            stycken.append(f"  {_dag(resa['fran'])}: {_kr(pris)}, {_resetext(res, typ)}{markering}")
    antal_dagar = len({r[1]["fran"] for v in rader.values() for r in v})
    stycken.append(f"Pris per person, enkel resa. {antal_dagar} dagar kontrollerade (var tredje dag).")

    ner = min((r[0] for r in rader["ner"]), default=None)
    hem = min((r[0] for r in rader["hem"]), default=None)
    delar = ([f"ner från {_kr(ner)}"] if ner else []) + ([f"hem från {_kr(hem)}"] if hem else [])
    vecka = idag.isocalendar()
    return {
        "id": f"sommar:{vecka[0]}-{vecka[1]}",
        "titel": f"☀️ Fitou sommaren {ar}: {', '.join(delar)}",
        "text": "\n".join(stycken),
        "url": basta_url or "https://www.google.com/travel/flights",
    }


# ---------------------------------------------------------------- huvudfunktion

def kontrollera(_webblasare=None, test=False):
    konfig = _las_json(KONFIG, {})
    nyckel = (konfig.get("serpapi_nyckel") or "").strip()
    if not nyckel:
        raise RuntimeError("serpapi_nyckel saknas i config.local.json")
    max_manad = int(konfig.get("fitou_max_per_manad", STANDARD_MAX_PER_MANAD))

    idag = dt.datetime.now(STOCKHOLM).date()
    lovresultat = _uppdatera_lov(konfig, idag)
    if isinstance(lovresultat, str):
        print(f"Fitouflyg: {lovresultat}")
    tillstand = _las_json(TILLSTAND, {})
    manad = idag.strftime("%Y-%m")
    if tillstand.get("manad") != manad:
        tillstand["manad"], tillstand["antal_manad"] = manad, 0
    uppgifter = tillstand.setdefault("uppgifter", {})

    resor = planera(idag)
    giltiga = {r["nyckel"] for r in resor}
    for k in list(uppgifter):
        if k not in giltiga:
            del uppgifter[k]

    nu = time.time()

    def angelagen(r):
        senast = (uppgifter.get(r["nyckel"]) or {}).get("senast", 0)
        if senast == 0:
            return 1e6 - (r["fran"] - idag).days  # aldrig kollad: närmast först
        return (nu - senast) / (_intervall_dagar(r, idag) * 86400)

    pa_tur = sorted((r for r in resor if angelagen(r) >= 1), key=angelagen, reverse=True)
    if test:
        pa_tur = [r for r in resor if r["typ"] == "helg"][:1] + [r for r in resor if r["typ"] == "ner"][:1]

    sokare = Sokare(nyckel)
    kvar = sokare.kvar() if pa_tur else 0
    for resa in pa_tur:
        if not test and (sokare.antal >= SOKNINGAR_PER_KORNING
                         or tillstand["antal_manad"] >= max_manad):
            break
        if kvar - sokare.antal <= RESERV_PA_KONTOT:
            print(f"Fitouflyg: bara {kvar - sokare.antal} sökningar kvar hos SerpApi – lämnar resten åt Flygfynd.")
            break
        res = _sok(sokare, resa)
        tillstand["antal_manad"] += 1
        post = uppgifter.setdefault(resa["nyckel"], {"historik": []})
        post["senast"] = nu
        if not res:
            post["resultat"], post["skal"] = None, []
            if test:
                print(f"{resa['namn']} {resa['fran']}: inga flyg hittades")
            continue
        tidigare = [p for _, p in post["historik"]]
        post["skal"] = bedom(res, tidigare)
        post["resultat"] = res
        post["historik"] = (post["historik"] + [[idag.isoformat(), res["pris"]]])[-HISTORIK_LANGD:]
        if test:
            print(f"{resa['namn']} {resa['typ']} {resa['fran']}–{resa['till']}: {_kr(res['pris'])} "
                  f"{FLYGPLATSER[res['flygplats']]} · Google: {res['niva']} {res['vanligt']} · {post['skal']}")
            print(f"   per flygplats: { {FLYGPLATSER[k]: round(v) for k, v in res['per'].items()} }")

    tillstand["senast_kvar"] = (kvar - sokare.antal) if pa_tur else tillstand.get("senast_kvar")
    _spara_tillstand(tillstand)

    fynd = []
    for resa in resor:
        post = uppgifter.get(resa["nyckel"]) or {}
        if resa["typ"] == "helg" and post.get("resultat") and post.get("skal"):
            fynd.append(_helgnotis(resa, post["resultat"], post["skal"]))
    if idag.weekday() == 6 or test:
        sammanfattning = _sommarsammanfattning(resor, uppgifter, idag)
        if sammanfattning:
            fynd.append(sammanfattning)
    return fynd


# ---------------------------------------------------------------- kommandorad

def _visa_plan():
    idag = dt.datetime.now(STOCKHOLM).date()
    resor = planera(idag)
    print("Långhelger och lov (tur och retur):")
    for r in resor:
        if r["typ"] == "helg":
            print(f"  {r['namn']:<18} {_period(r['fran'], r['till'])} {r['fran'].year}"
                  f"  – kollas var {_intervall_dagar(r, idag)}:e dag")
    sommar = [r for r in resor if r["typ"] == "ner"]
    if sommar:
        print(f"Sommaren: {len(sommar)} dagar ({_dag(sommar[0]['fran'])} – {_dag(sommar[-1]['fran'])}), "
              f"ner och hem, var {_intervall_dagar(sommar[0], idag)}:e dag")
    per_dag = sum(1 / _intervall_dagar(r, idag) for r in resor)
    print(f"Ungefär {per_dag * 30:.0f} sökningar i månaden behövs (tak {STANDARD_MAX_PER_MANAD}).")


def _visa_lage():
    idag = dt.datetime.now(STOCKHOLM).date()
    uppgifter = _las_json(TILLSTAND, {}).get("uppgifter", {})
    for r in planera(idag):
        post = uppgifter.get(r["nyckel"]) or {}
        res = post.get("resultat")
        if res:
            print(f"{r['typ']:<4} {_period(r['fran'], r['till']) if r['typ'] == 'helg' else _dag(r['fran']):<22} "
                  f"{_kr(res['pris']):>9} {FLYGPLATSER[res['flygplats']]:<11} {'; '.join(post.get('skal') or [])}")


if __name__ == "__main__":
    if "--lov" in sys.argv:
        sys.argv.append("--test")
        res = _uppdatera_lov(_las_json(KONFIG, {}), dt.datetime.now(STOCKHOLM).date(), tvinga=True)
        print(res if isinstance(res, str) else json.dumps({k: res[k] for k in ("lov", "sommarlov")},
                                                           ensure_ascii=False, indent=2))
    elif "--plan" in sys.argv:
        _visa_plan()
    elif "--lage" in sys.argv:
        _visa_lage()
    elif "--testa" in sys.argv:
        sys.argv.append("--test")
        for f in kontrollera(test=True):
            print("\nNOTIS:", f["titel"], "\n" + f["text"], "\n" + f["url"])
        print("\nKlart (ingen notis skickad, inget sparat).")
    else:
        print(__doc__)
