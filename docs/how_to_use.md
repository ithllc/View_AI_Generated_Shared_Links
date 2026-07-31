# How To Use

This document explains how to invoke the View AI Generated Shared Links application from both a human-driven CLI context and an automated AI Agent context.

## 1. Environment Configuration (OCR Vision)
If you wish to extract content using a Vision Model (highly recommended for websites utilizing Shadow DOMs, complex SVGs, or anti-scraping obfuscation like Google Gemini links), ensure you have an `.env` file created in the project root:

```ini
LOCAL_LLM_BASE_URL="http://192.168.1.168:4000/v1"
LOCAL_LLM_MODEL="gemma-4-26b"
```

If these values are omitted or the connection times out, the tool gracefully falls back to classical DOM text-extraction.

## 1b. Browser / Anti-Bot Configuration (Optional)

To reduce the chance of provider bot-detection (e.g. Google's reCAPTCHA interstitial on `share.google` links), the browser layer is hardened by default and configurable via `.env`:

```ini
# Prefer a real Chrome install over bundled Chromium (less detectable).
# Falls back to bundled Chromium automatically if unavailable.
# Set empty to force bundled Chromium.
BROWSER_CHANNEL="chrome"

# Run headless (default true). Set false for a headed session so you can
# clear the occasional CAPTCHA by hand — pair with USER_DATA_DIR below.
BROWSER_HEADLESS="true"

# Optional persistent profile dir: reuses cookies/consent across runs like a
# returning user, which lowers bot-risk scoring. Leave unset for ephemeral.
USER_DATA_DIR="/path/to/profile"

# Apply playwright-stealth patches (navigator.webdriver, plugins, WebGL
# vendor, UA/client-hint alignment, ...). Default true.
STEALTH_ENABLED="true"
```

## 1c. Resource Guards (hang & memory protection)

A fetch drives a real browser, so it is guarded against the two ways that can go wrong — hanging forever, and eating all your RAM. A watchdog samples the browser process tree while it runs and **kills it** if either ceiling is crossed, failing fast with a clear message instead of blocking your machine.

```ini
# Hard wall-clock ceiling for one fetch. If the browser hangs, it is killed. (s)
FETCH_TIMEOUT_SEC="90"

# Hard memory ceiling for the whole Chrome process tree. If exceeded, the tree
# is killed and the fetch aborts. A normal headless fetch uses a few hundred
# MB, well under this. (MB)
MEMORY_LIMIT_MB="1536"

# Launch Chrome with low-memory flags (fewer renderer processes, capped caches,
# no GPU/extensions/background work). Default true.
LOW_MEMORY="true"
```

If a run is ever force-killed (Ctrl-C, OOM, crash) and leaves an orphaned browser behind, reap it with:

```bash
python main.py cleanup
```

This only targets automation-controlled Chrome tied to this app's profiles — it never touches your normal browser.

> **Note on Google Search / AI Mode:** these defaults neutralise the JavaScript-level bot tells (verified: `navigator.webdriver=false`, honest UA + client hints, real WebGL renderer), but they cannot *guarantee* bypass. IP reputation and the TLS/HTTP2 fingerprint remain, and this surface is aggressively defended. If challenges persist, run headed (`BROWSER_HEADLESS=false`) with a persistent `USER_DATA_DIR`, and/or use a residential egress IP. Programmatic CAPTCHA-solving is intentionally **not** implemented. See [the incident report](./troubleshooting_playwright_timeout.md#follow-up-google-bot-detection-on-sharegoogle-links) for the full analysis.

## 2. CLI Usage

To fetch a new AI conversational link and turn it into Markdown, use the `fetch` command.

```bash
python main.py fetch "https://share.google/aimode/vie4Eq6HoeaCiDGsv"
```

**Expected outcomes:**
- On the first run, the tool spins up a headless browser, waits for the JS to load, human-scrolls down the page to trigger lazy-loaded text, overrides any collapsed boxes, and snaps a high-res screenshot (with multi-tier fallback if it hangs). It then uses OCR or DOM markup to dump the URL natively into Markdown.
- The screenshot is **best-effort** and used only for the optional OCR/Vision path. If every screenshot tier times out (common on Google's dynamically resizing `share.google/aimode` layout), the tool logs a warning, skips OCR, and still completes via DOM parsing — it will not crash. See [the Playwright timeout incident report](./troubleshooting_playwright_timeout.md) for details.
- Note: some providers (notably Google) may return an anti-bot CAPTCHA page to a headless request. In that case the fetch still succeeds, but the captured Markdown will contain the CAPTCHA notice rather than the conversation.
- On subsequent runs with the exact same URL, the application returns `Duplicate Detected!` and provides the cached path immediately without spawning a browser.
- Before execution, a deterministic cleanup runs automatically, removing any file in `saved_links` modified over 7 days ago.

To review what links your environment has collected so far:
```bash
python main.py list
```

### Warming a trusted session (for CAPTCHA-prone providers)

When a provider (e.g. Google) keeps returning a reCAPTCHA interstitial, you can solve the challenge **once by hand** in a real browser window and reuse that trusted session for later automated fetches. This requires a persistent profile (`USER_DATA_DIR`).

```bash
# .env must contain a persistent profile dir, e.g.:
#   USER_DATA_DIR="./.profile"
python main.py warm "https://share.google/aimode/xxxx"
```

The `warm` command opens a **headed** browser with the persistent profile, waits for you to solve the CAPTCHA / accept consent, and — when you press Enter in the terminal — saves the resulting cookies into the profile. Subsequent `python main.py fetch <same-url>` runs reuse that profile (they can stay headless) and are far less likely to be challenged.

**Display requirements — this needs a screen you can click:**

- **WSLg / Linux desktop, running as your normal (session-owning) user:** a browser window appears automatically; just interact with it.
- **Running as `root` under WSLg** (WSLg's display belongs to your normal user, so root can't attach to it) **or on a headless server:** use the bundled helper, which runs the browser inside its own virtual display exposed over VNC:

  ```bash
  # one-time: sudo apt-get install -y xvfb x11vnc fluxbox
  USER_DATA_DIR=./.profile ./scripts/warm_session.sh "https://share.google/aimode/xxxx"
  ```

  Then open any VNC viewer on Windows and connect to `localhost:5900` (WSL2 shares `localhost` with Windows). Solve the challenge in the window, then press Enter in the terminal.

### Interactive capture — the most reliable way past a CAPTCHA (recommended)

`warm` only saves cookies for later; **interactive capture** goes one step further: it opens the page in a real browser, lets *you* solve any CAPTCHA and load the full conversation, and then captures **that live tab** directly. Because a human drives a genuine session, providers like Google serve the real content instead of a bot challenge — there is no automated fingerprint to distrust.

```bash
python main.py fetch --interactive "https://share.google/aimode/xxxx"
```

This bypasses the cache (always captures fresh) and needs a display. As a desktop-session user the window just appears. Running headless / as `root`, use the bundled Xvfb + VNC helper:

```bash
# one-time: sudo apt-get install -y xvfb x11vnc fluxbox
./scripts/interactive_session.sh capture "https://share.google/aimode/xxxx"
# (./scripts/interactive_session.sh warm "<url>" does the warm flow instead)
```

Connect a VNC viewer to `localhost:5900`, solve the CAPTCHA / load the conversation, then press Enter in the terminal — the live page is saved as Markdown.

### Ingesting an already-saved page (no automation at all)

The most bulletproof option: open the link in *any* browser, save the fully-rendered page (`Ctrl+S` → "Webpage, HTML Only", or copy the DOM), and convert that file — nothing automated ever touches the provider.

```bash
python main.py ingest ./saved_page.html --url "https://share.google/aimode/xxxx"
```

`--url` is optional; it sets the provider and metadata. Without it the file is stored under the `generic` provider.

## 3. Agent Usage (Local API)

If you are writing a separate agent code that needs programmable access, spin up the server:
```bash
python main.py serve
```

Send a `POST` request to automatically fetch the content.

```bash
curl -X POST http://localhost:8000/api/fetch \
     -H "Content-Type: application/json" \
     -d '{"url": "https://chatgpt.com/share/xxxx"}'
```

The returned object tells your agent if the result was pulled fresh or fetched securely from the deduplication engine:

```json
{
  "saved_path": "/full/path/to/repo/saved_links/chatgpt/2026-07-30_Title_Hash.md",
  "provider": "chatgpt",
  "url": "https://chatgpt.com/share/xxxx",
  "is_cached": false
}
```
