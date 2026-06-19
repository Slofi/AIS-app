#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== AIS App install ==="

if [ ! -d "$HOME/.adsb-venv" ]; then
  python3 -m venv "$HOME/.adsb-venv"
fi
"$HOME/.adsb-venv/bin/pip" install -q -r "$DIR/requirements.txt"

mkdir -p "$DIR/static/lib"
for f in leaflet.css leaflet.js; do
  if [ ! -f "$DIR/static/lib/$f" ]; then
    echo "Downloading Leaflet $f..."
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/$f" -o "$DIR/static/lib/$f"
  fi
done

SERVICE_FILE="$HOME/.config/systemd/user/ais-app.service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=AIS App
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
Environment=AIS_APP_PORT=5410
Environment=AIS_UDP_PORT=10110
Environment=AIS_JSON_TCP_PORT=10111
ExecStart=$HOME/.adsb-venv/bin/python3 $DIR/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user start ais-app
echo "=== Done — http://localhost:5410 ==="
