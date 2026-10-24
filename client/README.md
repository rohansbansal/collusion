# Agent exchange client

An async Python library for trading agents. Requires Python 3.11+. It installs
independently of the exchange server and includes type information for editors.

## Install

From the repository root:

```sh
pip install ./client
```

Or from another agent project, after this package has been pushed to GitHub:

```sh
uv add "exchange-client @ git+https://github.com/rohansbansal/collusion.git#subdirectory=client"
```

Start the exchange separately with `cd exchange && uv run python main.py`.

## Trade

```python
import asyncio
from exchange_client import ExchangeClient, OrderNotOpen

async def main():
    async with ExchangeClient("my-agent", "http://127.0.0.1:8000") as exchange:
        fair = await exchange.fair()
        result = await exchange.buy(price=max(1, int(fair.price) - 10), quantity=5)
        print(result.order.order_id, result.order.status, result.trades)
        print(await exchange.orders())
        try:
            await exchange.cancel_order(result.order.order_id)
        except OrderNotOpen:
            pass  # The order filled before cancellation.

asyncio.run(main())
```

`buy`, `sell`, and `place_order(side, price, quantity)` submit GTC limit orders.
Prices are positive integer ticks (1–10^12) and quantities are integer units
(1–10^9). The fair uses the same tick units and is a floating point value.
Orders have `open`, `filled`, or `cancelled` status, `remaining` quantity, and a
`filled_quantity` property. Partial fills remain `open`.

## Stream order book and external fair

```python
from contextlib import aclosing
from exchange_client import ExchangeClient

async def watch():
    async with ExchangeClient("observer") as exchange:
        async with aclosing(exchange.stream(reconnect=True)) as feed:
            async for market in feed:
                print(market.fair.price, market.book.best_bid, market.book.best_ask)
                # await exchange.buy(price=..., quantity=...) when your strategy decides.
```

Each connection starts with current state. Updates include a full `book`, `fair`,
`sequence`, UTC `timestamp`, and `recent_trades`. `best_bid` and `best_ask` return
`None` when that side is empty. Use `snapshot()` for a one-time market snapshot.
Use `aclosing` when breaking out of a stream so its socket closes immediately.

Reconnection is opt-in, with up to five consecutive retries and exponential
backoff starting at 250 ms, capped at 30 seconds. Receiving a snapshot resets the
retry count. Configure `max_retries` and `retry_delay` on `stream()`. Authentication
and other permanent handshake errors propagate. Reconnection can miss intermediate
events; sequence gaps are expected. Recent trades are only the latest 100:
deduplicate by `trade_id` and reconcile your order states with `orders()`.

## Errors and lifecycle

API failures raise `ExchangeError` with `status_code`, `detail`, and `response`.
Subclasses are `OrderNotFound` (404), `OrderNotOpen` (409), and `InvalidOrder` (422).
Local input or response validation raises `pydantic.ValidationError`;
invalid connection options raise `ValueError`. Transport errors and timeouts
propagate from HTTPX or websockets. Requests never retry automatically: a timed-out
order might have been accepted, so reconcile with `orders()` before submitting
again. This simulator does not offer idempotency keys.

Use the async context manager or call `await exchange.aclose()` to release the
HTTP pool. Streams own separate sockets and should be closed with `aclosing`.
Models support `.model_dump()` or `.model_dump(mode="json")` for serialization.
One client represents one caller-supplied agent ID; the simulation has no
authentication or account balances.
