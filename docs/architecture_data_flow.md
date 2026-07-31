# Data Flow Architecture

This diagram illustrates how data transforms from a source (a live URL rendered in a browser, or an already-saved HTML file) through capture and formatting into stored Markdown. The Vision-LLM and DOM paths, and all three capture modes, converge on one formatter.

```mermaid
flowchart LR
    Origin[(AI Link URL)] --> P[Playwright Engine\nreal Chrome + stealth]
    File[(Saved HTML File)] --> RawHTML

    P --> |headless: auto scroll/expand| SC[Multi-Tier Capture]
    P --> |interactive: human solves CAPTCHA| SC
    P -.-> |wrapped by| GUARD{{Watchdog\ntimeout + memory kill}}

    SC --> |Image Bytes| VLLM{Local Vision LLM\nOptional / OCR}
    SC --> |Rendered HTML| RawHTML[HTML Content]

    VLLM --> |API 200 OK| FinalMD[Production Markdown]
    VLLM -.-> |Timeout/Error/No image| RawHTML

    RawHTML --> BS[BeautifulSoup]
    BS --> |decompose()| CleanDOM[Cleaned DOM]
    note1[Removed scripts, styles, noscript, svg] -.-> CleanDOM

    CleanDOM --> MDfy[markdownify]
    MDfy --> |Heading ATX, Strip links| RawMD[Raw Markdown]
    RawMD --> RegX[Regex Whitespace Cleaner]
    RegX --> FinalMD

    FinalMD --> Disk[(Storage Directory\nprovider/date_title_hash.md)]
    Disk --> |Read file| Consumer([AI Agent / User])
```
