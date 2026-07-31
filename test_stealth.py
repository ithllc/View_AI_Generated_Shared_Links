import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto("https://bot.sannysoft.com")
        print("Success")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
