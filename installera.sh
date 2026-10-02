#!/usr/bin/env bash
# Installerar bevakningarna på en Raspberry Pi (Raspberry Pi OS 64-bit).
# Kör från arkivets mapp:  bash installera.sh
set -euo pipefail
cd "$(dirname "$0")"
MAPP="$(pwd)"
ANVANDARE="$(whoami)"

echo "== Installerar systempaket (lösenord kan efterfrågas) =="
sudo apt-get update
sudo apt-get install -y python3-venv chromium git

echo "== Skapar Python-miljö =="
python3 -m venv venv
./venv/bin/pip install --upgrade pip >/dev/null
./venv/bin/pip install playwright

if [ ! -f config.local.json ]; then
  AMNE="bevakning-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cat > config.local.json <<EOF
{
  "ntfy_server": "https://ntfy.sh",
  "ntfy_amne": "$AMNE"
}
EOF
fi
AMNE="$(./venv/bin/python -c 'import json;print(json.load(open("config.local.json"))["ntfy_amne"])')"

chmod +x kor.sh

echo "== Ställer in automatisk körning var femte minut =="
sudo tee /etc/systemd/system/pi-bevakningar.service >/dev/null <<EOF
[Unit]
Description=Pi-bevakningar
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$ANVANDARE
WorkingDirectory=$MAPP
ExecStart=$MAPP/kor.sh
TimeoutStartSec=900
EOF

sudo tee /etc/systemd/system/pi-bevakningar.timer >/dev/null <<EOF
[Unit]
Description=Kör Pi-bevakningar var femte minut

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pi-bevakningar.timer

./venv/bin/python - <<EOF
import json, urllib.request
k = json.load(open("config.local.json"))
req = urllib.request.Request(f'{k["ntfy_server"]}/{k["ntfy_amne"]}',
    data="Pi-bevakningarna är igång.".encode(), headers={"Title": "Testnotis"}, method="POST")
urllib.request.urlopen(req, timeout=30).read()
EOF

echo
echo "================================================================"
echo " Klart! Prenumerera i ntfy-appen på ämnet:"
echo
echo "     $AMNE"
echo
echo " (En testnotis har just skickats dit.)"
echo "================================================================"
