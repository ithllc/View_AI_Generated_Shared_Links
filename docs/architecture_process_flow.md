# Process Flow Architecture

This diagram illustrates the operational process triggered whenever a capture request is initiated by a user or an agent. There are three capture modes — automated headless `fetch`, human-in-loop `fetch --interactive`, and `ingest` of an already-saved page — that converge on one shared extraction + storage pipeline.

```mermaid
flowchart TD
    A([Start Request]) --> B{Entry point?}
    B -->|CLI fetch URL| C[python main.py fetch URL]
    B -->|CLI fetch -i URL| CI[python main.py fetch --interactive URL]
    B -->|CLI ingest FILE| CG[python main.py ingest FILE]
    B -->|Agent| D[POST /api/fetch]

    C --> E[Deterministic Cleanup]
    D --> E
    CI --> E
    E --> F[Delete artifacts older than 7 days]
    F --> G[Extract Provider & Hash URL]

    G --> H{Hash Exists in Storage?}
    H -->|Yes, and not interactive| I[Return existing local Path]
    H -->|No / interactive re-capture| MODE{Capture mode}

    %% ---- Ingest path: no automation touches the provider ----
    CG --> GI[Read saved HTML file] --> EXTRACT

    %% ---- Browser paths launch a stealthed, low-memory Chrome ----
    MODE -->|headless fetch| J[Launch stealthed real Chrome - headless]
    MODE -->|interactive| JI[Launch stealthed real Chrome - headed]

    J --> GUARD[[Resource Watchdog: wall-clock + memory ceiling]]
    JI --> HUMAN[Human solves CAPTCHA / loads conversation, presses Enter]

    GUARD --> K[Wait for Network/JS/Fonts]
    K --> L[Simulate Human Scrolling 500px steps]
    L --> M[Force Expand Accordions & Modals]
    M --> CAP
    HUMAN --> CAP[Capture live page: HTML + title]

    CAP --> N{Multi-tier Screenshot Engine - best effort}
    N -->|Native Stitch| O[full_page Screenshot]
    N -->|Timeout 15s| P[Locator Container Screenshot]
    N -->|Timeout 10s| Q[Viewport Screenshot]
    N -->|All tiers fail| SKIP[Skip screenshot - DOM only, no crash]

    O & P & Q & SKIP --> EXTRACT{Extraction}

    EXTRACT --> R{LLM configured AND screenshot present?}
    R -->|Yes| S[Send Image + Prompt to Vision LLM]
    S --> T{LLM 200 OK?}
    T -->|Yes| U[Use LLM OCR Markdown]
    T -->|No / Timeout| V[DOM parse: BeautifulSoup + markdownify]
    R -->|No| V

    U --> X[Save Hash to Provider Directory]
    V --> X

    X --> I
    I --> Y([End: Provide Markdown to Agent/User])

    %% Watchdog abort: a hang or runaway Chrome tree is killed and reported
    GUARD -. hang or memory breach .-> KILL[Kill Chrome tree + reap stragglers]
    KILL --> ERR([Abort with clear error])
```
