import re
import base64
import aiohttp
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import markdownify
from src.config import LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL

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

async def fetch_and_parse_url(url: str) -> dict:
    provider = identify_provider(url)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
        
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
            await browser.close()
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

        await browser.close()

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
