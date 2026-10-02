#!/usr/bin/env bash
# Hämtar senaste bevakningarna från GitHub och kör dem.
# Körs automatiskt var femte minut av systemd-timern.
set -u
cd "$(dirname "$0")"
exec 9>/tmp/pi-bevakningar.lock
flock -n 9 || exit 0   # förra körningen pågår fortfarande

git pull --ff-only --quiet || echo "git pull misslyckades – kör befintlig version"
./venv/bin/python bevaka.py "$@"
