"""Run with `uv run python main.py` or `uv run uvicorn main:app`."""

from exchange.api import create_app

app = create_app()


def main():
    import uvicorn

    # State lives in memory: run exactly one worker per exchange.
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
