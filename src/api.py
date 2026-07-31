from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.fetcher import fetch_and_parse_url, identify_provider
from src.storage import save_markdown, list_saved_links, check_existing_url, cleanup_old_files

app = FastAPI(title="Shared AI Links API", description="API to fetch and convert AI shared links to markdown.")

class LinkRequest(BaseModel):
    url: str

class LinkResponse(BaseModel):
    saved_path: str
    provider: str
    url: str
    is_cached: bool = False

@app.post("/api/fetch", response_model=LinkResponse)
async def api_fetch_link(request: LinkRequest):
    try:
        # Deterministic cleanup
        cleanup_old_files(days=7)
        
        provider = identify_provider(request.url)
        existing_path = check_existing_url(request.url, provider)
        if existing_path:
            return LinkResponse(saved_path=existing_path, provider=provider, url=request.url, is_cached=True)

        data = await fetch_and_parse_url(request.url)
        saved_path = save_markdown(data)
        return LinkResponse(saved_path=saved_path, provider=data["provider"], url=data["url"], is_cached=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/links")
async def api_list_links():
    links = list_saved_links()
    return {"saved_links": links}
