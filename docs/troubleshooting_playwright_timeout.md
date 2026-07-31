# Incident Report & Troubleshooting: Playwright Screenshot Timeout

## Overview
When attempting to dynamically fetch and run OCR against complex AI-shared links (specifically `share.google` domains), the application hangs and eventually throws a fatal exception:
`Error: Failed to load URL: Page.screenshot: Timeout 30000ms exceeded.`

## Diagnostic Analysis
The terminal logged the following during the failure:
```
Call log:
  - taking page screenshot
  - waiting for fonts to load...
  - fonts loaded
```
This specific hang event occurs precisely at the `await page.screenshot(full_page=True)` execution block and happens *after* fonts signify they have loaded. 

**Root Cause:**
1. **Infinite Scroll / Dynamic Resizing:** Modern Google search/AI layouts heavily rely on DOM structures that infinitely stretch or dynamically resize as you scroll down. Playwright's `full_page=True` algorithm works by physically virtually scrolling the page, taking partial screenshots, and stitching them together. If the page's `document.body.scrollHeight` continually increases as it scrolls, Playwright enters an infinite loop until the overarching `30000ms` driver timeout kills it.
2. **Animation States:** CSS transitions or SVG loaders continually animating on the page can prevent Playwright from determining that the page has hit an "idle" visual state suitable for capturing.

## The Solution
To fix this and make the OCR screenshot capture resilient, we adapt the scraping strategy:
1. **Disable Animations:** Pass `animations="disabled"` to *every* screenshot tier (including the viewport fallback). This freezes all CSS/JS transitions immediately.
2. **Stricter Timeout on the Capture:** Limit the full-page screenshot attempt to 15 seconds instead of the global 30.
3. **Multi-Tier Fallback:** Try `full_page=True` first, then the `main` / `div[role="main"]` content locator, then a plain `full_page=False` viewport grab.

### The critical fix: a failed screenshot must never abort the fetch
The screenshot is consumed **only** by the optional LLM Vision/OCR path (Strategy 1). Classical DOM parsing (Strategy 2) works entirely off the page HTML, which is captured *before* the screenshot. The original code, however, took the screenshot inside the same `try` block that loads the page, so when **all** screenshot tiers timed out — as they do on the `share.google/aimode` layout — the exception propagated to the outer handler and re-raised `Failed to load URL`, aborting the whole run. This directly contradicted the documented promise that the tool "gracefully falls back to classical DOM text-extraction."

The capture logic now lives in a dedicated `_capture_screenshot(page)` helper and runs in its own `try/except` **after** the page HTML and title are already secured. If every tier fails, we log a warning, set `base64_image = None`, and continue. Strategy 1 is then guarded by `if LOCAL_LLM_BASE_URL and base64_image:`, so it is skipped when there is nothing to OCR, and Strategy 2 (DOM parsing) always produces a result.

The above changes have been implemented in `src/fetcher.py`.

## Follow-up: Google bot-detection on `share.google` links

Even with the timeout crash resolved, Google may respond to an automated request with an "unusual traffic" **reCAPTCHA interstitial** instead of the AI-mode conversation. When this happens the fetch still succeeds (no crash) but the captured Markdown contains the CAPTCHA notice rather than the conversation.

### How the bot is detected
Google runs an ML risk score over many signals. A default headless Playwright browser trips several at once:
1. **`navigator.webdriver === true`** — the single biggest tell for any CDP-driven browser.
2. **Headless fingerprint** — empty `navigator.plugins`, `WebGL` renderer reporting `SwiftShader` (software rasterizer), missing `window.chrome`, permissions-API inconsistencies.
3. **UA / client-hint mismatch** — the old code hardcoded a stale `Chrome/115 ... Windows` UA that did not match the real browser version or its `Sec-CH-UA` client hints. A mismatched fake UA is a *stronger* signal than the truth.
4. **TLS / HTTP2 fingerprint (JA3/JA4)** — Playwright's network stack negotiates differently from real Chrome. This lives below the JS layer and **cannot** be patched with any browser-side trick.
5. **IP reputation + robotic behavior** — datacenter/VPN IPs and instant-navigation/auto-scroll patterns.

### Hardening implemented (`src/fetcher.py`, `src/config.py`)
The following now runs by default and closes vectors #1–#3:
- **Real Chrome, not bundled Chromium:** launches with `channel="chrome"` (falls back to bundled Chromium automatically), giving a genuine WebGL renderer and consistent version/client-hints. Configurable via `BROWSER_CHANNEL`.
- **playwright-stealth:** patches `navigator.webdriver`, plugins, `window.chrome`, WebGL vendor, and UA/client-hint alignment. Toggle with `STEALTH_ENABLED`.
- **Honest UA:** the mismatched hardcoded User-Agent was removed; modern "new" headless Chrome sends a self-consistent UA.
- **Optional persistent profile:** set `USER_DATA_DIR` to reuse cookies/consent across runs like a returning user.
- **Optional headed mode:** set `BROWSER_HEADLESS=false` so a human can clear the occasional challenge once and the persistent profile keeps the session trusted.

Verified against a fingerprint probe: `navigator.webdriver` is now `false`, the UA is honest Chrome 133 with matching client hints, plugins are non-empty, `window.chrome` is present, and the WebGL renderer reports a real GPU (`Intel Iris OpenGL Engine`) instead of SwiftShader.

### Warming a trusted session
For challenge-prone providers, `python main.py warm <url>` (or `scripts/warm_session.sh` when running headless/as root) opens a headed browser with a persistent `USER_DATA_DIR` so you can solve the CAPTCHA once by hand; later `fetch` runs reuse that trusted profile. See [How To Use](./how_to_use.md#warming-a-trusted-session-for-captcha-prone-providers).

> **Implementation note:** `playwright-stealth`'s `Stealth().use_async(...)` auto-hook patches `launch()`/`new_context()` but **not** `launch_persistent_context()`. Persistent-profile contexts therefore need stealth applied explicitly (`await stealth.apply_stealth_async(context)`), otherwise `navigator.webdriver` and friends leak through on exactly the warmed sessions we most want to look human. `_new_context()` does this.

### What hardening does NOT solve
Passive hardening lowers the risk score but cannot *guarantee* passage on Google Search / AI Mode. Vectors #4 (TLS/HTTP2 fingerprint) and #5 (IP reputation) remain, and this surface is aggressively defended. If challenges persist:
- run **headed** with a **persistent `USER_DATA_DIR`** (clear one challenge by hand, stay trusted), and/or
- route through a **residential** egress IP.

**Not implemented (by design):** programmatic reCAPTCHA solving via third-party solver services. For Google Search AI Mode it is unreliable (risk-scored Enterprise reCAPTCHA often rejects farm-solved tokens) and is against Google's Terms of Service. The supported strategy is to *avoid triggering* the challenge, not to defeat it after the fact.
