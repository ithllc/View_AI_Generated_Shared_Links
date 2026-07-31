import os
import uuid
import time
import hashlib
from datetime import datetime
from pathlib import Path
from src.config import STORAGE_DIR

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def check_existing_url(url: str, provider: str) -> str | None:
    url_hash = get_url_hash(url)
    provider_dir = STORAGE_DIR / provider
    if provider_dir.exists():
        for f in provider_dir.glob(f"*_{url_hash}.md"):
            return str(f.resolve())
    return None

def save_markdown(data: dict) -> str:
    provider = data["provider"]
    title = data["title"]
    url = data["url"]
    markdown_content = data["markdown_content"]
    
    # Clean title for filename (alphanumeric and dashes/underscores)
    safe_title = "".join([c if c.isalnum() else "_" for c in title])
    if len(safe_title) > 50:
        safe_title = safe_title[:50]
        
    url_hash = get_url_hash(url)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    filename = f"{date_str}_{safe_title}_{url_hash}.md"
    
    provider_dir = STORAGE_DIR / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = provider_dir / filename
    
    # Write robust metadata block
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"---\n")
        f.write(f"Url: {url}\n")
        f.write(f"Provider: {provider}\n")
        f.write(f"Title: {title}\n")
        f.write(f"Date: {date_str}\n")
        f.write(f"---\n\n")
        f.write(f"# {title}\n\n")
        f.write(markdown_content)
        
    return str(filepath.resolve())

def list_saved_links():
    results = []
    for md_file in STORAGE_DIR.rglob("*.md"):
        results.append(str(md_file.resolve()))
    return results

def cleanup_old_files(days=7):
    """Deletes markdown files older than `days`."""
    now = time.time()
    cutoff = now - (days * 86400)
    cleaned_count = 0
    for md_file in STORAGE_DIR.rglob("*.md"):
        if md_file.stat().st_mtime < cutoff:
            md_file.unlink()
            cleaned_count += 1
    return cleaned_count
