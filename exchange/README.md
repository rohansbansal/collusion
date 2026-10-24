# Agent exchange

A single-market, in-memory simulated exchange. Agents submit limit orders through
HTTP and receive the aggregated order book and an independent external fair price
over WebSocket. No orders or liquidity are inserted automatically.

## Run

```sh
uv sync
uv run python main.py
```

The server listens on `http://127.0.0.1:8000`; interactive API docs are at `/docs`.
In a second terminal, run `uv run python example_agent.py` for a working agent.
Run tests with `uv run pytest`.

## Agent client

Run from this directory:

```python
import asyncio
from exchange import ExchangeClient

async def trade():
    async with ExchangeClient("agent-1") as agent:
        result = await agent.place_order("buy", price=9990, quantity=5)
        print(result["order"], result["trades"])
        print(await agent.orders())
        await agent.cancel_order(result["order"]["order_id"])
        async for snapshot in agent.stream():
            print(snapshot["book"], snapshot["fair"]["price"])

asyncio.run(trade())
```

The stream starts with current state on every connection, then publishes on every
accepted order, cancellation, and fair-price tick. Failures propagate to the agent,
which can reconnect by calling `stream()` again.

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /orders` | Submit `{ "agent_id": "a", "side": "buy", "price": 9990, "quantity": 5 }`; returns the order, its immediate trades, and market sequence |
| `GET /orders?agent_id=a` | All of the agent's orders, including filled and cancelled orders |
| `DELETE /orders/{order_id}?agent_id=a` | Cancel the unfilled portion of an open order |
| `GET /book` | Current full market snapshot |
| `GET /fair` | Current external fair price and its UTC update time |
| `WS /ws` | Full market snapshots, including book, external fair, and the latest 100 trades |

Prices are **positive integer ticks** and quantities are **positive integer units**.
For example, with a one-cent tick, `10000` means $100.00. Fair prices are floating
point values in the same tick units. Maximum order price is 10^12 ticks and maximum
quantity is 10^9 units. Buy orders match the lowest asks and sell orders match the
highest bids; ties use arrival order. Trades execute at the resting order's price.
Unfilled quantities remain on the book until filled or cancelled (GTC).
Order statuses are `open`, `filled`, and `cancelled`; a partially filled order stays
`open`, and `remaining` records the unfilled quantity even after cancellation.
Self-trades are permitted in this simulator.

Snapshots have this shape:

```json
{
  "type": "snapshot",
  "sequence": 1,
  "timestamp": "2026-10-24T17:00:00+00:00",
  "book": {"bids": [{"price": 9990, "quantity": 5}], "asks": []},
  "fair": {"price": 10000.0, "updated_at": "2026-10-24T17:00:00+00:00"},
  "recent_trades": []
}
```

Each client has a bounded queue: slow consumers receive the latest full snapshot,
so sequences may skip. `recent_trades` is a rolling window, not a lossless execution
feed; deduplicate by `trade_id`. Poll `/orders` to reconcile your fills. Invalid
orders return 422; unknown orders or mismatched agent IDs return 404; cancelling
an already filled or cancelled order returns 409.

## External fair-price process

By default, the external fair starts at 10,000 ticks and follows a positive random
walk every 250 ms, independently of the order book and trading activity:

`log(fair_next) = log(fair) + drift * dt + volatility * sqrt(dt) * N(0, 1)`

Log prices are bounded to keep fair prices between 1 and 10^12 ticks. The default
log drift is zero and volatility is 0.01 per square root second. Customize the
process by supplying a config when constructing the server:

```python
from exchange.api import MarketConfig, create_app

app = create_app(MarketConfig(
    initial_fair=10000, interval_seconds=0.25,
    volatility=0.01, drift=0.0, seed=42,
))
```

A fixed seed repeats the price path per tick across runs; wall-clock timestamps
and order arrival times are independent. The fair price is observable reference
data; it does not provide executable liquidity.

This is a local simulation: agent IDs are caller-supplied, with no authentication,
balances, position limits, fees, or persistence. All state resets at restart. Run
one server worker so every agent shares the same book and price process.
