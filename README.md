# View AI Generated Shared Links

This tool allows users and AI agents to fetch content from shared AI conversation links (e.g., ChatGPT, Claude, Gemini, Perplexity) and converts them into organized Markdown files for context retrieval.

## Features
- **Headless Browser Scraping:** Uses `Playwright` to navigate and fully render JavaScript-heavy AI shared links.
- **Anti-Bot Hardening:** Prefers a real Chrome install and applies `playwright-stealth` (neutralizing `navigator.webdriver`, WebGL/plugin tells, and UA/client-hint mismatches) to reduce provider bot-detection. Supports optional headed mode and a persistent profile (`USER_DATA_DIR`) for challenge-prone surfaces.
- **Human-like Lazy Loading:** Automatically performs dynamic interval scrolling and targets inner containers to ensure all lazy-loaded conversational nodes render before capture.
- **Multi-Tier Screenshot Engine:** Tries full-page stitching, degrades to specific `main` node locators, and safely falls back to viewport snapshots to guarantee capturing success without timing out.
- **Hybrid OCR LLM Extraction (Optional):** Employs a local Vision LLM via the V1 API to read the screenshots directly, gracefully falling back to DOM parsing if the visual read fails or times out.
- **Markdown Conversion**: Uses `markdownify` + `BeautifulSoup` to strip noise and save pure conversational paths to Markdown.
- **Provider-specific Organization**: Automatically categorizes downloaded contexts into `saved_links/<provider>/`.
- **Deduplication:** Hashing mechanism prevents identical URLs from being repeatedly processed.
- **Automatic Weekly Cleanup**: Automatically checks and removes artifacts older than 7 days to keep disk footprints low deterministically.
- **API & CLI Access**: Comes fully featured with a beautiful CLI (via `click` + `rich`) as well as a fast local REST API (`FastAPI`) making it effortless for other agents to consume.

## Prerequisites
- Python 3.10+
- Playwright browsers installed

## Installation

1. Clone the repository and setup your environment:
   ```bash
   git clone https://github.com/ithllc/View_AI_Generated_Shared_Links.git
   cd View_AI_Generated_Shared_Links
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Install Playwright browser dependencies:
   ```bash
   playwright install chromium
   ```

4. Configure your `.env` (Optional, for Visual OCR extraction):
   ```ini
   LOCAL_LLM_BASE_URL="http://192.168.1.168:4000/v1"
   LOCAL_LLM_MODEL="gemma-4-26b"
   ```

## Documentation
Please view the [docs folder](./docs/how_to_use.md) for how-to guides and architecture definitions.
