#!/usr/bin/env bash
#
# warm_session.sh — run the interactive `warm` command inside a self-owned
# virtual display (Xvfb) exposed over VNC.
#
# Why: on WSLg (and headless servers) the built-in desktop session is owned by
# a different user, so a headed browser launched as root cannot attach to it
# and hangs. An Xvfb display is owned by whoever starts it, sidestepping that
# entirely. You view/click it from Windows with any VNC viewer.
#
# Usage:
#   USER_DATA_DIR=./.profile ./scripts/warm_session.sh "https://share.google/aimode/xxxx"
#
# Then point a VNC viewer (TightVNC/RealVNC/UltraVNC on Windows) at
# localhost:5900, solve the CAPTCHA / accept consent in the browser window,
# and press Enter in THIS terminal to save the session and tear everything down.
#
# Requires: xvfb, x11vnc, fluxbox  (sudo apt-get install -y xvfb x11vnc fluxbox)

set -euo pipefail

DISPLAY_NUM="${WARM_DISPLAY:-:99}"
VNC_PORT="${WARM_VNC_PORT:-5900}"
SCREEN_GEOMETRY="${WARM_GEOMETRY:-1440x900x24}"
URL="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present and not already active.
if [[ -z "${VIRTUAL_ENV:-}" && -f "venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

for bin in Xvfb x11vnc; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "Error: '$bin' not found. Install with: sudo apt-get install -y xvfb x11vnc fluxbox" >&2
    exit 1
  fi
done

XVFB_PID=""; VNC_PID=""; WM_PID=""
cleanup() {
  echo "Cleaning up virtual display..."
  [[ -n "$VNC_PID"  ]] && kill "$VNC_PID"  2>/dev/null || true
  [[ -n "$WM_PID"   ]] && kill "$WM_PID"   2>/dev/null || true
  [[ -n "$XVFB_PID" ]] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting Xvfb on ${DISPLAY_NUM} (${SCREEN_GEOMETRY})..."
Xvfb "$DISPLAY_NUM" -screen 0 "$SCREEN_GEOMETRY" >/tmp/warm_xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2

# Window manager is best-effort: Chrome renders without one, but a WM gives
# reliable input focus for clicking the CAPTCHA.
if command -v fluxbox >/dev/null 2>&1; then
  DISPLAY="$DISPLAY_NUM" fluxbox >/tmp/warm_fluxbox.log 2>&1 &
  WM_PID=$!
  sleep 1
fi

echo "Starting x11vnc on localhost:${VNC_PORT} (no password, localhost-only)..."
x11vnc -display "$DISPLAY_NUM" -localhost -nopw -forever -shared -quiet \
  -rfbport "$VNC_PORT" >/tmp/warm_x11vnc.log 2>&1 &
VNC_PID=$!
sleep 1

cat <<EOF

============================================================================
 VNC ready. From Windows, open a VNC viewer and connect to:  localhost:${VNC_PORT}
 (WSL2 shares localhost with Windows, so no extra networking is needed.)

 In the browser window that appears: solve the CAPTCHA / accept consent,
 then return here and press Enter to save the session.
============================================================================

EOF

DISPLAY="$DISPLAY_NUM" python main.py warm "$URL"
