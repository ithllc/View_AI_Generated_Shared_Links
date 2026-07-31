import re
import asyncio
import base64
import aiohttp
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
import markdownify
from src.config import (
    LOCAL_LLM_BASE_URL,
    LOCAL_LLM_MODEL,
    BROWSER_CHANNEL,
    BROWSER_HEADLESS,
    USER_DATA_DIR,
    STEALTH_ENABLED,
)

def identify_provider(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if 'chatgpt.com' in domain or 'openai.com' in domain:
        return 'chatgpt'
    elif 'claude.ai' in domain or 'anthropic.com' in domain:
        return 'claude'
    elif 'gemini.google.com' in domain or 'share.google' in domain:
        return 'gemini'
    elif 'perplexity.ai' in domain:
        return 'perplexity'
    return 'generic'

async def _capture_screenshot(page) -> bytes:
    """Best-effort multi-tier screenshot capture for the optional OCR/Vision path.

    Tries a full-page stitch first, then the primary content locator, then a
    plain viewport grab. Animations are disabled on every attempt to avoid the
    known infinite-stitch / never-idle hang on dynamically resizing pages
    (see docs/troubleshooting_playwright_timeout.md). Raises only if every tier
    fails; callers must treat that as non-fatal and fall back to DOM parsing.
    """
    try:
        return await page.screenshot(full_page=True, type="jpeg", quality=80, timeout=15000, animations="disabled")
    except Exception as full_err:
        print(f"Warning: Full page screenshot timed out/failed. Trying locator mapping... ({full_err})")

    try:
        return await page.locator("main, div[role='main']").first.screenshot(type="jpeg", quality=80, timeout=10000, animations="disabled")
    except Exception as loc_err:
        print(f"Warning: Locator mapping failed. Falling back to viewport... ({loc_err})")

    # Final tier: plain viewport grab, animations still disabled so it cannot
    # hang the same way the full-page stitch did.
    return await page.screenshot(full_page=False, type="jpeg", quality=80, timeout=10000, animations="disabled")

def _make_playwright():
    """Return ``(playwright_context_manager, stealth_or_None)``.

    When stealth is enabled we drive Playwright through
    ``Stealth().use_async(...)`` so that ``launch()`` + ``new_context()`` are
    auto-hooked. NOTE: that auto-hook does NOT cover
    ``launch_persistent_context`` -- persistent contexts must have stealth
    applied explicitly (see ``_new_context``), otherwise ``navigator.webdriver``
    et al. leak through. The returned stealth object is used for exactly that.
    """
    if STEALTH_ENABLED:
        stealth = Stealth()
        return stealth.use_async(async_playwright()), stealth
    return async_playwright(), None


async def _new_context(p, stealth=None, headless=None):
    """Create a browsing context tuned to minimise bot-detection.

    Prefers a real Chrome install (``channel="chrome"``) over bundled Chromium
    because a genuine Chrome build leaks far fewer headless tells (real WebGL
    renderer, consistent version/client-hints). Falls back to bundled Chromium
    if the channel is unavailable. When ``USER_DATA_DIR`` is set, a persistent
    profile is used so cookies/consent state carry across runs like a returning
    user. Returns ``(context, aclose)`` where ``aclose`` is an awaitable that
    tears the browser/context down.

    ``stealth`` is the object returned by :func:`_make_playwright`. For
    persistent contexts (which the ``use_async`` hook misses) we apply it
    explicitly so the anti-detection patches actually take effect.

    Note: we intentionally do NOT spoof a hardcoded User-Agent. Real Chrome (in
    modern "new" headless) sends an honest, self-consistent UA + client hints;
    playwright-stealth aligns the JS-visible surface. A mismatched fake UA is a
    stronger bot signal than the truth.
    """
    if headless is None:
        headless = BROWSER_HEADLESS
    channels_to_try = [BROWSER_CHANNEL, None] if BROWSER_CHANNEL else [None]
    last_err = None
    for channel in channels_to_try:
        kwargs = {"headless": headless}
        if channel:
            kwargs["channel"] = channel
        # Headed real Chrome refuses to run as root without --no-sandbox (common
        # in containers / WSL, where interactive `warm` sessions run). The
        # primary headless fetch path runs fine as root without it, so we keep
        # the flag off there to preserve the cleanest possible fingerprint.
        if channel and not headless:
            kwargs["args"] = ["--no-sandbox"]
        try:
            if USER_DATA_DIR:
                context = await p.chromium.launch_persistent_context(USER_DATA_DIR, **kwargs)
                # use_async does NOT hook persistent contexts -- apply explicitly.
                if stealth is not None:
                    await stealth.apply_stealth_async(context)

                async def _aclose():
                    await context.close()

                cleanup = _aclose
            else:
                browser = await p.chromium.launch(**kwargs)
                context = await browser.new_context()

                async def _aclose():
                    await browser.close()

                cleanup = _aclose

            if channel is None and BROWSER_CHANNEL:
                print("Warning: real Chrome channel unavailable; using bundled Chromium (more detectable).")
            return context, cleanup
        except Exception as e:
            last_err = e
            if channel:
                first_line = str(e).splitlines()[0] if str(e) else repr(e)
                print(f"Warning: browser channel '{channel}' failed to launch, trying bundled Chromium... ({first_line})")
            continue

    raise Exception(f"Failed to launch any browser: {last_err}")


async def fetch_and_parse_url(url: str) -> dict:
    provider = identify_provider(url)

    # Apply stealth to every context/page created within this block (patches
    # navigator.webdriver, plugins, window.chrome, WebGL vendor, UA/client-hint
    # consistency, ...). If disabled, use a plain Playwright context.
    pw_cm, stealth = _make_playwright()

    async with pw_cm as p:
        context, cleanup = await _new_context(p, stealth=stealth)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # Scroll to the bottom of the page to trigger any lazy-loaded content
            js_scroll = """
            async () => {
                await new Promise((resolve) => {
                    let totalScroll = 0;
                    const distance = 500;
                    const timer = setInterval(() => {
                        window.scrollBy(0, distance);
                        
                        // Also attempt to scroll inner containers (common in React/SPA apps)
                        const scrollableBoxes = document.querySelectorAll('main, div[role="main"], div');
                        scrollableBoxes.forEach(box => {
                            if (box.scrollHeight > box.clientHeight) {
                                box.scrollBy(0, distance);
                            }
                        });

                        totalScroll += distance;
                        if (totalScroll >= document.body.scrollHeight || totalScroll > 20000) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 250);
                });
            }
            """
            await page.evaluate(js_scroll)
            await page.wait_for_timeout(2000) # Let lazy loaded content settle
            
            # Scroll back to the top to reset the viewport for screenshot
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(1000)

            # Force expand any collapsed boxes by clicking standard attributes
            js_expand = """
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                try { el.click(); } catch(e) {}
            });
            document.querySelectorAll('button').forEach(btn => {
                if(btn.innerText && btn.innerText.toLowerCase().includes('more')) {
                    try { btn.click(); } catch(e) {}
                }
            });
            """
            await page.evaluate(js_expand)
            await page.wait_for_timeout(2000) # wait for animations to resolve

            html_content = await page.content()
            title = await page.title()

        except Exception as e:
            await cleanup()
            raise Exception(f"Failed to load URL: {str(e)}")

        # Take a screenshot for the optional OCR/Vision path. This is strictly
        # best-effort: the screenshot is ONLY consumed by the LLM vision strategy
        # below, while DOM parsing relies solely on the HTML already captured
        # above. A screenshot failure must therefore never abort the fetch --
        # otherwise pages that are un-screenshottable (e.g. Google's infinitely
        # resizing AI-mode layout) would fail entirely instead of degrading to
        # DOM extraction.
        base64_image = None
        try:
            screenshot_bytes = await _capture_screenshot(page)
            base64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
        except Exception as screenshot_err:
            print(f"Warning: All screenshot tiers failed. Skipping OCR and using DOM parsing. ({screenshot_err})")

        await cleanup()

    markdown_text = None
    
    # Strategy 1: Attempt LLM Vision extraction if configured in ENV and we
    # actually captured a screenshot to read.
    if LOCAL_LLM_BASE_URL and base64_image:
        try:
            payload = {
                "model": LOCAL_LLM_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text", 
                                "text": "Extract all the conversational text content, questions, and responses from this screenshot. Format the output neatly in Markdown. Do not include any other commentary."
                            },
                            {
                                "type": "image_url", 
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            }
                        ]
                    }
                ],
                "max_tokens": 4096
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{LOCAL_LLM_BASE_URL.rstrip('/')}/chat/completions", 
                    json=payload, 
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if 'choices' in result and len(result['choices']) > 0:
                            markdown_text = result['choices'][0]['message'].get('content', '')
                    else:
                        print(f"LLM extraction failed with status {resp.status}. Falling back to DOM parsing...")
        except Exception as e:
            print(f"LLM extraction encountered an error: {e}. Falling back to DOM parsing...")
    
    # Strategy 2: Fallback to DOM parsing
    if not markdown_text:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Less aggressive decomposition
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        # More flexible main content finding
        main_content = soup.find("main") or soup.find("div", {"role": "main"}) or soup.find("div", {"id": "__next"}) or soup.find("div", {"id": "root"}) or soup.body
            
        if not main_content:
            markdown_text = "Could not parse content from the provided URL."
        else:
            markdown_text = markdownify.markdownify(str(main_content), heading_style="ATX", strip=['a', 'img'])

        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text).strip()

    return {
        "url": url,
        "provider": provider,
        "title": title.strip() if title else "Untitled",
        "markdown_content": markdown_text
    }


async def warm_profile(url: str | None = None):
    """Open a *headed*, real-Chrome browser using the persistent profile so a
    human can solve a CAPTCHA / accept a consent dialog by hand. When the window
    is closed, the resulting cookies + consent state are persisted in
    ``USER_DATA_DIR``, so subsequent (even headless) ``fetch`` runs using the
    same profile are treated like a returning, trusted user.

    Requires ``USER_DATA_DIR`` to be set -- warming an ephemeral profile is
    pointless because the state would be discarded immediately. Requires a
    display: under WSLg / a desktop the window appears normally; on a headless
    server run it under Xvfb + a VNC viewer so you can actually interact.
    """
    if not USER_DATA_DIR:
        raise Exception(
            "USER_DATA_DIR is not set. Warming only makes sense with a persistent "
            "profile. Set USER_DATA_DIR in your .env (e.g. USER_DATA_DIR=\"./.profile\") "
            "and retry."
        )

    pw_cm, stealth = _make_playwright()
    async with pw_cm as p:
        # Force headed regardless of BROWSER_HEADLESS -- the whole point is manual
        # interaction. Reuse the persistent-profile + channel-fallback logic.
        context, cleanup = await _new_context(p, stealth=stealth, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()

        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"Warning: initial navigation had an issue ({e}). You can still navigate manually in the window.")

        # Block on human interaction without freezing the event loop.
        await asyncio.to_thread(
            input,
            "\n>>> A browser window should now be open.\n"
            ">>> Solve any CAPTCHA / accept any consent dialog, then press Enter HERE to save the session and close.\n"
        )

        await cleanup()
