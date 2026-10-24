"""HTTP order entry and WebSocket snapshots for a single simulated market."""

import asyncio
import math
import random
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool

from .engine import MatchingEngine, OrderRequest
from .simulation import Simulation


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


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
        self.simulation = Simulation(self, config.seed)
        self.history: deque[dict] = deque(maxlen=240)
        self.sample_history()

    def sample_history(self):
        fair = self.fair()
        self.simulation.history.append(fair["price"])
        self.history.append({
            "timestamp": fair["updated_at"], "fair": fair["price"],
            "last": self.engine.trades[-1]["price"] if self.engine.trades else None,
        })

    def fair(self) -> dict:
        return {"price": math.exp(self.log_fair), "updated_at": self.fair_updated_at}

    def snapshot(self) -> dict:
        return {
            "type": "snapshot", "sequence": self.sequence, "timestamp": now(),
            "book": self.engine.book(), "fair": self.fair(),
            "recent_trades": list(self.engine.trades),
            "simulation": self.simulation.snapshot(), "history": list(self.history),
            "stats": {"trades": self.engine.trade_count, "volume": self.engine.volume,
                      "open_orders": len(self.engine.active),
                      "last_price": self.engine.trades[-1]["price"] if self.engine.trades else None},
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
                self.sample_history()
                self.publish()


def create_app(config: MarketConfig | None = None, *, demo: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.market = Market(config or MarketConfig())
        app.state.market.simulation.set_running(demo)
        task = asyncio.create_task(app.state.market.run_prices())
        agents_task = asyncio.create_task(app.state.market.simulation.run())
        try:
            yield
        finally:
            task.cancel()
            agents_task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with suppress(asyncio.CancelledError):
                await agents_task

    app = FastAPI(title="Agent Exchange", lifespan=lifespan)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(static / "index.html")

    @app.get("/agents")
    async def agents():
        async with app.state.market.lock:
            return app.state.market.simulation.snapshot()

    @app.post("/simulation/{action}")
    async def simulation_control(action: str):
        if action not in ("start", "pause"):
            raise HTTPException(404, "Unknown simulation action")
        market = app.state.market
        async with market.lock:
            market.simulation.set_running(action == "start")
            market.publish()
            return market.simulation.snapshot()

    @app.patch("/agents/{agent_id}")
    async def agent_control(agent_id: str, request: EnabledRequest):
        market = app.state.market
        async with market.lock:
            try:
                market.simulation.set_enabled(agent_id, request.enabled)
            except KeyError as exc:
                raise HTTPException(404, "Unknown simulation agent") from exc
            market.publish()
            return market.simulation.snapshot()

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
