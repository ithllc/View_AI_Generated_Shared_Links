import re
import asyncio
import base64
import aiohttp
from pathlib import Path
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
    LOW_MEMORY,
    FETCH_TIMEOUT_SEC,
    MEMORY_LIMIT_MB,
    GUARD_POLL_SEC,
)
from src.guard import run_guarded, kill_own_browsers

# Low-memory Chrome flags: cap renderer processes/caches, drop GPU + background
# work. Keeps a headless fetch to a few hundred MB instead of gigabytes.
_LOW_MEMORY_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-features=site-per-process,TranslateUI",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=512",
    "--disk-cache-size=1048576",
    "--no-first-run",
    "--no-default-browser-check",
]

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
        args = list(_LOW_MEMORY_ARGS) if LOW_MEMORY else []
        # Headed real Chrome refuses to run as root without --no-sandbox (common
        # in containers / WSL, where interactive `warm` sessions run). The
        # primary headless fetch path runs fine as root without it, so we keep
        # the flag off there to preserve the cleanest possible fingerprint.
        if channel and not headless:
            args.append("--no-sandbox")
        if args:
            kwargs["args"] = args
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


async def _browser_capture(url: str):
    """Drive the browser and return ``(html_content, title, base64_image)``.

    Wrapped by :func:`run_guarded` in :func:`fetch_and_parse_url` so a hung page
    or a runaway Chrome tree is killed by the watchdog rather than blocking
    forever / eating RAM. The browser is always torn down via the finally block.
    """
    # Apply stealth to every context/page created within this block (patches
    # navigator.webdriver, plugins, window.chrome, WebGL vendor, UA/client-hint
    # consistency, ...). If disabled, use a plain Playwright context.
    pw_cm, stealth = _make_playwright()

    async with pw_cm as p:
        context, cleanup = await _new_context(p, stealth=stealth)
        try:
            page = await context.new_page()
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

            return html_content, title, base64_image
        finally:
            # Always release the browser, even on cancellation from the watchdog.
            try:
                await cleanup()
            except Exception:
                pass


def _dom_to_markdown(html_content: str) -> str:
    """Convert a rendered HTML document to clean conversational Markdown."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Less aggressive decomposition
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    # More flexible main content finding
    main_content = (
        soup.find("main")
        or soup.find("div", {"role": "main"})
        or soup.find("div", {"id": "__next"})
        or soup.find("div", {"id": "root"})
        or soup.body
    )

    if not main_content:
        return "Could not parse content from the provided document."

    markdown_text = markdownify.markdownify(str(main_content), heading_style="ATX", strip=['a', 'img'])
    return re.sub(r'\n{3,}', '\n\n', markdown_text).strip()


async def _extract_markdown(url, provider, title, html_content, base64_image):
    """Turn captured page data into the saved-record dict.

    Strategy 1: optional local LLM Vision OCR (only if configured *and* a
    screenshot was captured). Strategy 2: DOM parsing. Shared by every capture
    path (headless fetch, interactive capture, and file ingest).
    """
    markdown_text = None

    # Strategy 1: LLM Vision extraction (configured + screenshot available).
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

    # Strategy 2: DOM parsing.
    if not markdown_text:
        markdown_text = _dom_to_markdown(html_content)

    return {
        "url": url,
        "provider": provider,
        "title": title.strip() if title else "Untitled",
        "markdown_content": markdown_text
    }


async def fetch_and_parse_url(url: str) -> dict:
    provider = identify_provider(url)

    # Run the browser work under the resource watchdog: a hang (wall-clock) or a
    # runaway Chrome tree (memory) is killed and reported, never left to block.
    try:
        html_content, title, base64_image = await run_guarded(
            _browser_capture(url),
            timeout_sec=FETCH_TIMEOUT_SEC,
            mem_limit_mb=MEMORY_LIMIT_MB,
            poll_sec=GUARD_POLL_SEC,
        )
    except Exception as e:
        kill_own_browsers()  # belt-and-suspenders: reap anything still lingering
        raise Exception(f"Failed to load URL: {str(e)}")

    return await _extract_markdown(url, provider, title, html_content, base64_image)


async def interactive_capture(url: str) -> dict:
    """Option 2: open a real, *headed* browser, let a human load/solve the page,
    then capture that live tab and convert it to Markdown.

    Because the human drives a genuine session (real cookies, real interaction),
    providers like Google serve the actual content instead of a bot CAPTCHA --
    there is no automated fingerprint to distrust. Uses the persistent profile
    when ``USER_DATA_DIR`` is set so the trust carries across runs.

    Needs a display: run as a desktop-session user, or via
    ``scripts/interactive_session.sh capture <url>`` (Xvfb + VNC) when headless
    or running as root. No watchdog timeout here -- a human is in the loop.
    """
    provider = identify_provider(url)
    pw_cm, stealth = _make_playwright()
    async with pw_cm as p:
        context, cleanup = await _new_context(p, stealth=stealth, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"Warning: initial navigation had an issue ({e}). You can still navigate manually in the window.")

            await asyncio.to_thread(
                input,
                "\n>>> A browser window should now be open.\n"
                ">>> Solve any CAPTCHA and make sure the full conversation is on screen,\n"
                ">>> then press Enter HERE to capture the page.\n"
            )

            try:
                await page.wait_for_timeout(500)  # let any final render settle
            except Exception:
                pass

            html_content = await page.content()
            title = await page.title()

            base64_image = None
            try:
                screenshot_bytes = await _capture_screenshot(page)
                base64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
            except Exception as screenshot_err:
                print(f"Warning: screenshot failed; using DOM parsing only. ({screenshot_err})")

            return await _extract_markdown(url, provider, title, html_content, base64_image)
        finally:
            try:
                await cleanup()
            except Exception:
                pass


def ingest_html_file(path: str, url: str | None = None) -> dict:
    """Option 3: convert an already-saved HTML page into a Markdown record.

    Zero anti-bot surface -- no automation ever touches the provider. The user
    saves the fully-rendered page from any browser and points this at the file.
    ``url`` (optional) sets the provider + metadata; otherwise a ``file://`` URL
    is derived so dedup/storage still work.
    """
    p = Path(path)
    if not p.is_file():
        raise Exception(f"File not found: {path}")

    html_content = p.read_text(encoding="utf-8", errors="replace")
    provider = identify_provider(url) if url else "generic"

    parsed_title = BeautifulSoup(html_content, "html.parser").title
    title = parsed_title.get_text().strip() if (parsed_title and parsed_title.get_text().strip()) else p.stem

    return {
        "url": url or p.resolve().as_uri(),
        "provider": provider,
        "title": title,
        "markdown_content": _dom_to_markdown(html_content),
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
