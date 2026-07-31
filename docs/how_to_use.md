# How To Use

This document explains how to invoke the View AI Generated Shared Links application from both a human-driven CLI context and an automated AI Agent context.

## 1. Environment Configuration (OCR Vision)
If you wish to extract content using a Vision Model (highly recommended for websites utilizing Shadow DOMs, complex SVGs, or anti-scraping obfuscation like Google Gemini links), ensure you have an `.env` file created in the project root:

```ini
LOCAL_LLM_BASE_URL="http://192.168.1.168:4000/v1"
LOCAL_LLM_MODEL="gemma-4-26b"
```

If these values are omitted or the connection times out, the tool gracefully falls back to classical DOM text-extraction.

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
