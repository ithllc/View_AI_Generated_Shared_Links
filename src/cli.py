import asyncio
import click
from rich.console import Console
from src.fetcher import fetch_and_parse_url, identify_provider, warm_profile
from src.storage import save_markdown, list_saved_links, check_existing_url, cleanup_old_files
from src.config import USER_DATA_DIR

console = Console()

@click.group()
def cli():
    """CLI to fetch AI shared links and save as Markdown context."""
    pass

@cli.command()
@click.argument("url")
def fetch(url: str):
    """Fetch an AI shared link and save as Markdown."""
    
    # 1. Deterministic weekly cleanup process
    cleaned = cleanup_old_files(days=7)
    if cleaned > 0:
        console.print(f"[dim]Weekly cleanup task ran automatically. Removed {cleaned} old artifact(s).[/dim]")

    console.print(f"[bold blue]Processing url:[/bold blue] {url}")
    
    # 2. Prevent duplicate fetches
    provider = identify_provider(url)
    existing_path = check_existing_url(url, provider)
    if existing_path:
        console.print(f"[bold yellow]Notice:[/bold yellow] This URL has already been fetched!")
        console.print(f"[bold green]Success![/bold green] Returning cached context from:\n{existing_path}")
        return

    async def run():
        try:
            data = await fetch_and_parse_url(url)
            path = save_markdown(data)
            console.print(f"[bold green]Success![/bold green] Saved context to:\n{path}")
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {str(e)}")
        
    asyncio.run(run())

@cli.command()
@click.argument("url", required=False)
def warm(url: str):
    """Open a headed browser to solve a CAPTCHA/consent by hand, then persist the session.

    Use this once against a challenge-prone provider (e.g. Google) before `fetch`.
    Requires USER_DATA_DIR to be set so the trusted session survives across runs.
    Optionally pass the URL you intend to fetch so it opens directly.
    """
    if not USER_DATA_DIR:
        console.print(
            "[bold red]Error:[/bold red] USER_DATA_DIR is not set.\n"
            "Add a persistent profile dir to your .env, e.g.:\n"
            "  [dim]USER_DATA_DIR=\"./.profile\"[/dim]\n"
            "then re-run this command."
        )
        return

    console.print(f"[bold blue]Warming persistent profile:[/bold blue] {USER_DATA_DIR}")
    console.print("[dim]A real browser window will open (WSLg/desktop). Interact with it, then press Enter in this terminal.[/dim]")

    async def run():
        try:
            await warm_profile(url)
            console.print(
                "[bold green]Session saved.[/bold green] Cookies/consent are stored in the profile.\n"
                "Subsequent [bold]fetch[/bold] runs using the same USER_DATA_DIR will reuse this trusted session."
            )
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {str(e)}")

    asyncio.run(run())

@cli.command()
def list():
    """List all saved AI links locally."""
    console.print("[bold blue]Saved Context Links:[/bold blue]")
    links = list_saved_links()
    if not links:
        console.print("No saved links found.")
    for link in links:
        console.print(f"- {link}")

if __name__ == "__main__":
    cli()
