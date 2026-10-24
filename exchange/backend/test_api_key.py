"""Make one small LangChain request to verify the local OpenAI configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY was not found in the environment or .env")

    from langchain_openai import ChatOpenAI

    response = ChatOpenAI(model=model, temperature=0).invoke(
        "Reply with exactly: banana market connection works"
    )
    print(f"Model: {model}")
    print(response.content)


if __name__ == "__main__":
    main()