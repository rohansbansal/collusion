"""HTTP entrypoint for running a banana-market experiment."""

import os
import json
import queue
import threading
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from starlette.responses import StreamingResponse

from .agents import BuyerAgent, BuyerDecision
from .continuous import LiveMarket, LiveSettings
from .experiment import Experiment, RoundResult, SellerTurn
from .market import Quote, Sale
from .message_bus import MarketMessage, PrivateMessageBoard

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


live_markets: dict[str, LiveMarket] = {}


class ExperimentRequest(BaseModel):
    rounds: int = Field(default=1, gt=0, le=100)
    buyers: int = Field(default=100, ge=1, le=100)
    buyer_budget: Decimal = Field(default=Decimal("10.00"), ge=0)
    buyer_max_price: Decimal = Field(default=Decimal("1.50"), gt=0)
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-6.1-sol"))


def make_buyers(request: ExperimentRequest) -> list[BuyerAgent]:
    # Fixed, reproducible valuation spread: 60–140% of the typical maximum price.
    return [BuyerAgent(
        name=f"buyer-{n + 1}", budget=request.buyer_budget,
        willingness_to_pay=(request.buyer_max_price * (
            Decimal("1") if request.buyers == 1 else
            Decimal("0.6") + Decimal("0.8") * n / (request.buyers - 1)
        )).quantize(Decimal("0.01")),
    ) for n in range(request.buyers)]


class ExperimentResponse(BaseModel):
    rounds: int
    buyer_decisions: list[BuyerDecision]
    quotes: list[Quote]
    purchases: list[Sale]
    seller_revenue: dict[str, Decimal]
    messages: list[MarketMessage]
    seller_turns: list[SellerTurn]
    round_results: list[RoundResult]
    public_messages: list[MarketMessage]
    private_message_boards: list[PrivateMessageBoard]


class StatusResponse(BaseModel):
    status: str
    frontend: str
    api_key_configured: bool


def create_app() -> FastAPI:
    app = FastAPI(title="Banana Market")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["POST"],
        allow_headers=["*"],
    )

    @app.get("/", response_model=StatusResponse)
    def status() -> StatusResponse:
        return StatusResponse(
            status="ok",
            frontend="Run the Svelte app from exchange/frontend",
            api_key_configured=bool(os.getenv("OPENAI_API_KEY")),
        )

    @app.post("/run_experiment", response_model=ExperimentResponse)
    def run_experiment(request: ExperimentRequest) -> ExperimentResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for LangChain agents")
        try:
            experiment = Experiment(
                buyers=make_buyers(request),
                rounds=request.rounds,
                model=request.model,
            )
            purchases = experiment.run()
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return ExperimentResponse(
            buyer_decisions=experiment.buyer_decisions,
            rounds=experiment.round,
            quotes=experiment.market.quotes(),
            purchases=purchases,
            seller_revenue={seller.name: seller.revenue for seller in experiment.market.sellers},
            messages=experiment.bus.messages,
            seller_turns=experiment.seller_turns,
            round_results=experiment.round_results,
            public_messages=[message for message in experiment.bus.messages if message.recipient is None],
            private_message_boards=[PrivateMessageBoard(
                owner=seller.name,
                messages=[message for message in experiment.bus.messages if message.recipient == seller.name],
            ) for seller in experiment.market.sellers],
        )

    @app.post("/run_experiment/stream")
    def stream_experiment(request: ExperimentRequest):
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for LangChain agents")

        events: queue.Queue[tuple[str, dict]] = queue.Queue()

        def run() -> None:
            try:
                experiment = Experiment(
                    buyers=make_buyers(request),
                    rounds=request.rounds,
                    model=request.model,
                )
                events.put(("status", {"message": "The sellers are getting ready."}))
                purchases = experiment.run(
                    on_turn=lambda turn: events.put(("turn", turn.model_dump(mode="json"))),
                    on_buyer=lambda decision: events.put(("buyer", decision.model_dump(mode="json"))),
                    on_round=lambda round_result: events.put(("round", round_result.model_dump(mode="json"))),
                )
                result = ExperimentResponse(
                    buyer_decisions=experiment.buyer_decisions,
                    rounds=experiment.round,
                    quotes=experiment.market.quotes(),
                    purchases=purchases,
                    seller_revenue={seller.name: seller.revenue for seller in experiment.market.sellers},
                    messages=experiment.bus.messages,
                    seller_turns=experiment.seller_turns,
                    round_results=experiment.round_results,
                    public_messages=[message for message in experiment.bus.messages if message.recipient is None],
                    private_message_boards=[PrivateMessageBoard(
                        owner=seller.name,
                        messages=[message for message in experiment.bus.messages if message.recipient == seller.name],
                    ) for seller in experiment.market.sellers],
                )
                events.put(("done", result.model_dump(mode="json")))
            except Exception as error:
                events.put(("error", {"message": str(error)}))

        threading.Thread(target=run, daemon=True).start()

        def generate():
            while True:
                event, data = events.get()
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                if event in {"done", "error"}:
                    break

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/market/stream")
    async def stream_market(settings: LiveSettings):
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required")
        engine = LiveMarket(settings)
        live_markets[engine.run_id] = engine
        async def events():
            stream = engine.stream()
            try:
                async for event in stream:
                    yield event
            finally:
                await stream.aclose()
                live_markets.pop(engine.run_id, None)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/market/{run_id}/probe")
    async def undercut_probe(run_id: str):
        engine = live_markets.get(run_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Market is no longer running")
        return {"applied": await engine.probe()}

    return app


app = create_app()