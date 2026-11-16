"""Nyckelbyte – tar emot en ny Claude-API-nyckel krypterad via det offentliga arkivet.

Engångsfunktion (november 2026). Arkivet är offentligt, så nyckeln får aldrig ligga
där i klartext. I stället:

1. Pi:n skapar ett eget nyckelpar med openssl (privata delen lämnar aldrig Pi:n,
   state/nyckelbyte/) och publicerar den publika delen på ett ntfy-ämne.
2. Claude krypterar den nya API-nyckeln med den publika delen och lägger den
   krypterade filen i data/nyckelbyte.enc.
3. Pi:n dekrypterar, provar nyckeln mot Anthropics API med ett minimalt anrop och
   byter först när det fungerar. Den gamla nyckeln sparas som reserv i
   config.local.json ("claude_nyckel_reserv", "claude_arbetsyta_reserv").

Efter bytet gör modulen ingenting mer och kan tas bort ur arkivet.
"""

import base64
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

NAMN = "Nyckelbyte"
INTERVALL_MINUTER = 5
AKTIV = True
BEHOVER_WEBBLASARE = False
PRIORITET = "default"

AMNE = "pi-nyckelbyte-4f0efaab9e52dee2"
ROT = Path(__file__).resolve().parent.parent
KONFIG = ROT / "config.local.json"
MAPP = ROT / "state" / "nyckelbyte"
KRYPTERAD = ROT / "data" / "nyckelbyte.enc"
OAEP = ["-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"]


def _kor(*argument, indata=None):
    return subprocess.run(argument, input=indata, capture_output=True, check=True).stdout


def _prova(nyckel, arbetsyta):
    huvuden = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
               "Authorization": "Bearer " + nyckel}
    if arbetsyta:
        huvuden["anthropic-workspace-id"] = arbetsyta
    kropp = json.dumps({"model": "claude-haiku-5-5", "max_tokens": 5,
                        "messages": [{"role": "user", "content": "Svara bara: ok"}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=kropp,
                                 method="POST", headers=huvuden)
    try:
        with urllib.request.urlopen(req, timeout=60) as svar:
            svar.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"den nya nyckeln godtogs inte ({e.code}): "
                           f"{e.read().decode('utf-8', 'replace')[:200]}")


def _rapportera(text):
    """Lägesrapport utan hemligheter, så att Claude kan se att bytet gick igenom."""
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{AMNE}", data=text.encode("utf-8"),
                                     method="POST", headers={"Title": "pi-status"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def kontrollera(_webblasare=None):
    try:
        return _kontrollera()
    except Exception as fel:
        _rapportera(f"fel: {str(fel)[:200]}")
        raise


def _kontrollera():
    if (MAPP / "klar").exists():
        if not (MAPP / "rapporterad").exists():
            _rapportera("klar: nyckeln provad och bytt")
            (MAPP / "rapporterad").write_text("ja")
        return []
    MAPP.mkdir(parents=True, exist_ok=True)
    privat, publik = MAPP / "privat.pem", MAPP / "publik.pem"

    if not privat.exists():
        _kor("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
             "-out", str(privat))
        privat.chmod(0o600)
        _kor("openssl", "pkey", "-in", str(privat), "-pubout", "-out", str(publik))

    if not (MAPP / "publicerad").exists():
        req = urllib.request.Request(f"https://ntfy.sh/{AMNE}", data=publik.read_bytes(),
                                     method="POST", headers={"Title": "pi-publik-nyckel"})
        urllib.request.urlopen(req, timeout=30).read()
        (MAPP / "publicerad").write_text("ja")
        print("Nyckelbyte: publik nyckel publicerad, väntar på krypterad API-nyckel.")

    if not KRYPTERAD.exists():
        return []

    chiffer = base64.b64decode(KRYPTERAD.read_text().strip())
    klartext = _kor("openssl", "pkeyutl", "-decrypt", "-inkey", str(privat), *OAEP, indata=chiffer)
    data = json.loads(klartext.decode("utf-8"))
    nyckel = data["nyckel"].strip()
    arbetsyta = (data.get("arbetsyta") or "").strip()
    if not nyckel.startswith("sk-ant-"):
        raise RuntimeError("det dekrypterade ser inte ut som en API-nyckel")

    _prova(nyckel, arbetsyta)

    konfig = json.loads(KONFIG.read_text(encoding="utf-8"))
    if konfig.get("claude_nyckel") and konfig["claude_nyckel"] != nyckel:
        konfig["claude_nyckel_reserv"] = konfig["claude_nyckel"]
        konfig["claude_arbetsyta_reserv"] = konfig.get("claude_arbetsyta", "")
    konfig["claude_nyckel"] = nyckel
    if arbetsyta:
        konfig["claude_arbetsyta"] = arbetsyta
    else:
        konfig.pop("claude_arbetsyta", None)
    tmp = KONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(konfig, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(KONFIG)
    (MAPP / "klar").write_text("ja")
    _rapportera("klar: nyckeln provad och bytt")
    (MAPP / "rapporterad").write_text("ja")
    return [{"id": "nyckelbyte-klar", "titel": "🔑 Claude-nyckeln är bytt",
             "text": "Pi:n använder nu API-nyckeln från papperskvarnen-kontot, där Max-abonnemangets "
                     "krediter ingår. Den gamla nyckeln finns kvar som reserv.",
             "url": None}]
