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

## Known follow-up: Google bot-detection on `share.google` links
Even with the timeout crash resolved, Google may respond to a headless/datacenter request with an "unusual traffic" **CAPTCHA interstitial** instead of the AI-mode conversation. When this happens the fetch now succeeds (no crash) but the saved Markdown contains the CAPTCHA notice rather than the conversation. This is an anti-bot concern distinct from the screenshot timeout; mitigating it (e.g. `playwright_stealth`, residential egress, authenticated sessions) is tracked separately and is not part of this fix.
