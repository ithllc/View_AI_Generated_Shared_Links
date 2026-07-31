import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "saved_links"

os.makedirs(STORAGE_DIR, exist_ok=True)

LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "gemma-4-26b")


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- Browser / anti-bot hardening -------------------------------------------
# Prefer a real Chrome install over bundled Chromium: a genuine Chrome build
# has a far less detectable fingerprint (real WebGL renderer, consistent
# version/client-hints, etc.). Falls back to bundled Chromium automatically if
# the channel is unavailable. Set to an empty string to force bundled Chromium.
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "chrome")

# Run headless by default. Set BROWSER_HEADLESS=false for a headed session
# (best paired with USER_DATA_DIR so a human can clear the occasional
# challenge once and stay trusted). Modern Chromium runs "new" headless, which
# is much closer to headed Chrome than the old headless mode.
BROWSER_HEADLESS = _env_bool("BROWSER_HEADLESS", True)

# Optional persistent profile directory. When set, the browser reuses cookies,
# consent state, and local storage across runs like a returning user, which
# dramatically lowers bot-risk scoring. Leave unset for an ephemeral context.
USER_DATA_DIR = os.getenv("USER_DATA_DIR") or None

# Apply playwright-stealth patches (navigator.webdriver, plugins, window.chrome,
# WebGL vendor, UA/client-hints alignment, ...). On by default; set
# STEALTH_ENABLED=false to disable.
STEALTH_ENABLED = _env_bool("STEALTH_ENABLED", True)
