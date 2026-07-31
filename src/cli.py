import asyncio
import click
from rich.console import Console
from src.fetcher import (
    fetch_and_parse_url,
    identify_provider,
    warm_profile,
    interactive_capture,
    ingest_html_file,
)
from src.storage import save_markdown, list_saved_links, check_existing_url, cleanup_old_files
from src.config import USER_DATA_DIR
from src.guard import kill_stragglers

console = Console()

@click.group()
def cli():
    """CLI to fetch AI shared links and save as Markdown context."""
    pass

@cli.command()
@click.argument("url")
@click.option(
    "--interactive", "-i", is_flag=True,
    help="Open a real browser, let you solve any CAPTCHA / load the page, then "
         "capture that live tab. Needs a display (see scripts/interactive_session.sh). "
         "Bypasses the cache to force a fresh capture.",
)
def fetch(url: str, interactive: bool):
    """Fetch an AI shared link and save as Markdown."""

    # 1. Deterministic weekly cleanup process
    cleaned = cleanup_old_files(days=7)
    if cleaned > 0:
        console.print(f"[dim]Weekly cleanup task ran automatically. Removed {cleaned} old artifact(s).[/dim]")

    console.print(f"[bold blue]Processing url:[/bold blue] {url}")

    # 2. Prevent duplicate fetches (interactive mode always re-captures fresh).
    provider = identify_provider(url)
    existing_path = check_existing_url(url, provider)
    if existing_path and not interactive:
        console.print(f"[bold yellow]Notice:[/bold yellow] This URL has already been fetched!")
        console.print(f"[bold green]Success![/bold green] Returning cached context from:\n{existing_path}")
        return
    if existing_path and interactive:
        console.print(f"[dim]A cached copy exists; interactive mode will capture a fresh version.[/dim]")

    async def run():
        try:
            if interactive:
                console.print("[dim]Opening a real browser — interact with it, then press Enter in this terminal.[/dim]")
                data = await interactive_capture(url)
            else:
                data = await fetch_and_parse_url(url)
            path = save_markdown(data)
            console.print(f"[bold green]Success![/bold green] Saved context to:\n{path}")
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {str(e)}")

    asyncio.run(run())

@cli.command()
@click.argument("file")
@click.option("--url", default=None, help="Original URL of the page (sets provider + metadata).")
def ingest(file: str, url: str):
    """Convert an already-saved HTML page into a Markdown record.

    No automation touches the provider: open the link in any browser, save the
    fully-rendered page, and point this at the .html file.
    """
    cleanup_old_files(days=7)
    try:
        data = ingest_html_file(file, url)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {str(e)}")
        return

    existing_path = check_existing_url(data["url"], data["provider"])
    if existing_path:
        console.print(f"[bold yellow]Notice:[/bold yellow] This page has already been ingested!")
        console.print(f"[bold green]Success![/bold green] Returning cached context from:\n{existing_path}")
        return

    path = save_markdown(data)
    console.print(f"[bold green]Success![/bold green] Ingested and saved context to:\n{path}")

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
def cleanup():
    """Kill orphaned automation-browser processes left by a crashed/killed run.

    Only targets Chrome processes that are automation-controlled AND tied to
    this app's profiles -- never your normal browser.
    """
    killed = kill_stragglers(extra_profile_dir=USER_DATA_DIR)
    if killed:
        console.print(f"[bold green]Reaped[/bold green] {killed} straggler browser process(es).")
    else:
        console.print("No straggler browser processes found.")

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
