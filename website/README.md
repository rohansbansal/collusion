# Inside the Exchange

An illustrated field guide to the exchange, eight Qwen traders, oracle incentives,
the Sentinel governor, and experiment design. The interactive examples are
educational and do not connect to the live exchange.

Preview from the repository root:

```sh
python3 -m http.server 8766 --directory website/dist
```

Open http://127.0.0.1:8766. No frontend dependencies or build step are required.

Run the arithmetic and page-reference checks:

```sh
node website/checks.mjs
```

The live exchange runs separately: `cd exchange && uv run python main.py`.
The site includes architecture diagrams, a trading-round walkthrough, an order
matching example, agent profiles, an oracle/P&L calculator, an oversight-mode
comparison, and an implementation/API reference.

This directory mirrors the static source published through Sites. Its managed
hosting checkout is excluded from the parent Git repository. No credentials or
experiment journals are included in this website.
