"""Run with `uv run python main.py` or `uv run uvicorn main:app`."""

from datetime import datetime, timezone
from pathlib import Path
import os

from dotenv import load_dotenv

from exchange.api import MarketConfig, create_app
from exchange.llm import LLMConfig

root = Path(__file__).resolve().parent
load_dotenv(root.parent / ".env")
load_dotenv(root / ".env")
session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
app = create_app(MarketConfig(volatility=0.001, seed=int(os.getenv("MARKET_SEED", "42"))), demo=True,
                 llm_config=LLMConfig.from_env(), log_path=root / "runs" / f"{session}.jsonl")


def main():
    import uvicorn

    # State lives in memory: run exactly one worker per exchange.
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
