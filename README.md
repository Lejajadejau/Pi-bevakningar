# Pi-bevakningar

Automatiska bevakningar som körs på en Raspberry Pi och skickar pushnotiser
till mobilen via appen **ntfy** när något nytt dyker upp.

- `bevaka.py` – motorn. Kör de bevakningar som är på tur och minns vad som redan rapporterats.
- `bevakningar/` – en fil per bevakning. Nya filer här plockas upp automatiskt.
- `kor.sh` – hämtar senaste versionen från GitHub och kör motorn (körs var femte minut).
- `installera.sh` – engångsinstallation på Pi:n.

Pi:n hämtar ändringar från GitHub automatiskt, så nya bevakningar börjar gälla
inom fem minuter utan att någon rör Pi:n.

## Installation på Pi:n (en gång)

Kräver Raspberry Pi OS **64-bit**. Kontrollera med `uname -m` – det ska stå `aarch64`.

**1. Skapa en nyckel som ger Pi:n läsrätt till arkivet**

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/pi_bevakningar
cat ~/.ssh/pi_bevakningar.pub
```

Kopiera raden som skrivs ut. Gå på GitHub till arkivet → **Settings → Deploy keys →
Add deploy key**. Klistra in raden, döp den till `raspberry`, och låt *Allow write
access* vara **avbockad**. Spara.

**2. Hämta arkivet**

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-bevakningar
  HostName github.com
  User git
  IdentityFile ~/.ssh/pi_bevakningar
  IdentitiesOnly yes
EOF
git clone github-bevakningar:Lejajadejau/pi-bevakningar.git ~/pi-bevakningar
```

Svara `yes` om den frågar om GitHubs fingeravtryck.

**3. Installera**

```bash
cd ~/pi-bevakningar
bash installera.sh
```

Det tar några minuter. När den är klar skrivs ett **ntfy-ämne** ut,
t.ex. `bevakning-3fa9…`.

**4. Prenumerera i ntfy-appen**

Öppna ntfy på mobilen → **+** → skriv in ämnet exakt → Subscribe.
Ämnet fungerar som ett lösenord: den som känner till det kan läsa notiserna,
så dela det inte.

## Bra att kunna

| Vad | Kommando |
|---|---|
| Testa alla bevakningar nu, utan notiser | `cd ~/pi-bevakningar && ./kor.sh --alla --test` |
| Köra alla bevakningar nu, med notiser | `./kor.sh --alla` |
| Skicka en testnotis | `curl -d "Test" ntfy.sh/DITT-ÄMNE` |
| Se loggen | `journalctl -u pi-bevakningar -n 50` |
| Pausa allt | `sudo systemctl stop pi-bevakningar.timer` |
| Starta igen | `sudo systemctl start pi-bevakningar.timer` |

En enskild bevakning pausas genom att sätta `AKTIV = False` i dess fil.

## Skriva en ny bevakning

Lägg en fil i `bevakningar/`:

```python
NAMN = "Läsbart namn"
INTERVALL_MINUTER = 60
AKTIV = True
BEHOVER_WEBBLASARE = False   # True ger en Chromium-webbläsare (Playwright)

def kontrollera(webblasare):
    # Returnera en lista med fynd. Bara fynd med nytt id ger notis.
    return [{"id": "unik-nyckel", "text": "Kort text i notisen", "url": "https://..."}]
```

Om en bevakning går fel skickas högst en varningsnotis per dygn.

## HD-bevakningen och Google-dokumentet

`bevakningar/hd_avgoranden.py` kollar en gång per dygn domstolarnas öppna API
(Sök rättspraxis) efter nya avgöranden från Högsta domstolen. Bara prejudikat
räknas – prövningstillstånd och senare NJA-referat hoppas över.

För varje nytt avgörande hämtas hela avgörandet som PDF och sammanfattas av
Googles Gemini (fråga, bakgrund, HD:s bedömning, utgång, betydelse, skiljaktiga).
Notisen får en kort sammanfattning och hela sammanfattningen skrivs in överst i
Google-dokumentet *Claude HD-bevakning* via ett litet Apps Script i dokumentet
(`google/hd_dokument.gs`). Gemini anropas bara en gång per avgörande.

Inställningar på Pi:n, i `config.local.json`:

```json
"hd_dokument_url": "webbapp-adressen från Apps Script (slutar med /exec)",
"hd_dokument_nyckel": "samma hemliga nyckel som i skriptet",
"gemini_nyckel": "API-nyckel från aistudio.google.com",
"gemini_modell": "valfritt, t.ex. gemini-2.5-pro"
```

Utan Gemini-nyckel används HD:s egen korta sammanfattning. Om något går fel
kommer notisen ändå, Pi:n försöker igen nästa dygn och varnar högst en gång per dygn.

| Vad | Kommando |
|---|---|
| Visa de tre senaste HD-avgörandena | `./venv/bin/python bevakningar/hd_avgoranden.py --senaste` |
| Prova Gemini-sammanfattning av det senaste | `./venv/bin/python bevakningar/hd_avgoranden.py --testa` |
| Fyll på dokumentet med alla avgöranden sedan ett datum | `./venv/bin/python bevakningar/hd_avgoranden.py --fyll-pa 2026-09-01` |

## Principer

Bevakningarna läser bara. De bokar, köper eller fyller aldrig i något, och de
försöker inte ta sig förbi inloggningar eller robotspärrar.
