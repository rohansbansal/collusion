"""HTTP order entry and WebSocket snapshots for a single simulated market."""

import asyncio
import math
import random
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone

import anyio
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from .engine import MatchingEngine, OrderRequest


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MarketConfig:
    initial_fair: float = 10_000
    interval_seconds: float = 0.25
    volatility: float = 0.01  # fractional volatility per square root second
    drift: float = 0.0  # log-price drift per second
    seed: int | None = None

    def __post_init__(self):
        for name in ("initial_fair", "interval_seconds", "volatility", "drift"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not 1 <= self.initial_fair <= 10**12 or self.interval_seconds <= 0 or self.volatility < 0:
            raise ValueError("Fair must be in [1, 10^12], interval > 0, and volatility >= 0")


class Market:
    def __init__(self, config: MarketConfig):
        self.config = config
        self.engine = MatchingEngine()
        self.lock = asyncio.Lock()
        self.subscribers: set[asyncio.Queue] = set()
        self.rng = random.Random(config.seed)
        self.log_fair = math.log(config.initial_fair)
        self.fair_updated_at = now()
        self.sequence = 0

    def fair(self) -> dict:
        return {"price": math.exp(self.log_fair), "updated_at": self.fair_updated_at}

    def snapshot(self) -> dict:
        return {
            "type": "snapshot", "sequence": self.sequence, "timestamp": now(),
            "book": self.engine.book(), "fair": self.fair(),
            "recent_trades": list(self.engine.trades),
        }

    def publish(self):
        self.sequence += 1
        snapshot = self.snapshot()
        for queue in self.subscribers:
            # Every event is a full snapshot. Slow clients get the latest state.
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    def step_fair(self):
        dt = self.config.interval_seconds
        self.log_fair += self.config.drift * dt + self.config.volatility * math.sqrt(dt) * self.rng.gauss(0, 1)
        self.log_fair = min(math.log(10**12), max(0.0, self.log_fair))
        self.fair_updated_at = now()

    async def run_prices(self):
        while True:
            await asyncio.sleep(self.config.interval_seconds)
            async with self.lock:
                self.step_fair()
                self.publish()


def create_app(config: MarketConfig | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.market = Market(config or MarketConfig())
        task = asyncio.create_task(app.state.market.run_prices())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Agent Exchange", lifespan=lifespan)

    @app.get("/book")
    async def book():
        async with app.state.market.lock:
            return app.state.market.snapshot()

    @app.get("/fair")
    async def fair():
        return app.state.market.fair()

    @app.post("/orders", status_code=201)
    async def place_order(request: OrderRequest):
        market = app.state.market
        async with market.lock:
            result = market.engine.place(request)
            market.publish()
            return {**result, "sequence": market.sequence}

    @app.get("/orders")
    async def orders(agent_id: str = Query(min_length=1, max_length=128)):
        async with app.state.market.lock:
            return {"orders": app.state.market.engine.agent_orders(agent_id)}

    @app.delete("/orders/{order_id}")
    async def cancel_order(order_id: str, agent_id: str = Query(min_length=1, max_length=128)):
        market = app.state.market
        async with market.lock:
            try:
                order = market.engine.cancel(order_id, agent_id)
            except KeyError as exc:
                raise HTTPException(404, str(exc.args[0])) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            market.publish()
            return {"order": order, "sequence": market.sequence}

    @app.websocket("/ws")
    async def stream(websocket: WebSocket):
        await websocket.accept()
        market = app.state.market
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        async with market.lock:
            market.subscribers.add(queue)
            queue.put_nowait(market.snapshot())

        try:
            async with anyio.create_task_group() as tasks:
                async def send():
                    try:
                        while True:
                            await websocket.send_json(await queue.get())
                    except (WebSocketDisconnect, OSError):
                        pass
                    finally:
                        tasks.cancel_scope.cancel()

                async def receive():
                    try:
                        while True:
                            message = await websocket.receive()
                            if message["type"] == "websocket.disconnect":
                                return
                    finally:
                        tasks.cancel_scope.cancel()

                tasks.start_soon(send)
                tasks.start_soon(receive)
        finally:
            market.subscribers.discard(queue)

    return app
