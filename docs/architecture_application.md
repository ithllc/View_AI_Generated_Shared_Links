# Application Architecture

This diagram illustrates the application layers and their corresponding file modules.

```mermaid
classDiagram
    class main {
        - CLI entrypoint (`cli()`)
        - Uvicorn Server execution
    }
    
    class api {
        + POST /api/fetch
        + GET /api/links
    }
    
    class cli {
        + @command fetch
        + @command list
    }
    
    class fetcher {
        + identify_provider(url)
        + async fetch_and_parse_url(url)
    }
    
    class storage {
        + get_url_hash(url)
        + check_existing_url(url, provider)
        + cleanup_old_files(days)
        + save_markdown(data)
        + list_saved_links()
    }
    
    main --> cli : fallback
    main --> api : if arg == 'serve'
    
    api --> fetcher : fetch link
    api --> storage : format & dedup & clean
    
    cli --> fetcher : fetch link
    cli --> storage : format & dedup & clean
```
