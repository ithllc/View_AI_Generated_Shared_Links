import asyncio
from src.fetcher import fetch_and_parse_url

async def main():
    print("Testing fetch...")
    try:
        res = await fetch_and_parse_url("https://share.google/aimode/vie4Eq6HoeaCiDGsv")
        print(res['markdown_content'])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
