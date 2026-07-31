# Data Flow Architecture

This diagram illustrates how data transforms from a raw URL through browser rendering, multi-tier capturing, and formatting.

```mermaid
flowchart LR
    Origin[(AI Link URL)] --> P[Playwright Engine]
    P --> |Dynamic Load + Lazy Scroll| SC[Multi-Tier Capture]
    
    SC --> |Image Bytes| VLLM{Local Vision LLM\nOptional / OCR}
    SC --> |Raw DOM Tree| BS[BeautifulSoup\nFallback]
    
    VLLM --> |API 200 OK| FinalMD[Production Markdown]
    VLLM -.-> |Timeout/Error| BS
    
    BS --> |decompose()| CleanDOM[Cleaned DOM]
    note1[Removed scripts, navbars, sidebars] -.-> CleanDOM
    
    CleanDOM --> MDfy[markdownify]
    MDfy --> |Heading ATX, Strip links| RawMD[Raw Markdown]
    
    RawMD --> RegX[Regex Whitespace Cleaner]
    RegX --> FinalMD
    
    FinalMD --> Disk[(Storage Directory)]
    Disk --> |Read file| Consumer([AI Agent / User])
```
