#!/bin/bash
set -euo pipefail

mkdir -p /tmp/.X11-unix /app/chrome-profile /app/data /app/logs
rm -f /tmp/.X99-lock

Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR >/app/logs/xvfb.log 2>&1 &
sleep 1
fluxbox >/app/logs/fluxbox.log 2>&1 &
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw -quiet >/app/logs/x11vnc.log 2>&1 &

exec python -m src.main
