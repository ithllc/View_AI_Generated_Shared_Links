import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        import uvicorn
        print("Starting FastAPI Server on http://localhost:8000")
        uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
    else:
        from src.cli import cli
        cli()
