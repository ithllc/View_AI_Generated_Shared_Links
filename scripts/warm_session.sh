#!/usr/bin/env bash
#
# warm_session.sh — backwards-compatible wrapper around interactive_session.sh.
# Opens a headed browser (inside Xvfb + VNC) to warm a persistent session:
# solve a CAPTCHA once, and the cookies persist in USER_DATA_DIR for later
# fetches. To capture a page's content interactively instead, use:
#   ./scripts/interactive_session.sh capture "<url>"
#
# Usage:
#   USER_DATA_DIR=./.profile ./scripts/warm_session.sh "https://share.google/aimode/xxxx"

exec "$(dirname "${BASH_SOURCE[0]}")/interactive_session.sh" warm "$@"
