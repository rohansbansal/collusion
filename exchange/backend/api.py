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

from .agents import BuyerAgent
from .experiment import Experiment, RoundResult, SellerTurn
from .market import Quote, Sale
from .message_bus import MarketMessage, PrivateMessageBoard

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class ExperimentRequest(BaseModel):
    rounds: int = Field(default=1, gt=0, le=100)
    buyers: int = Field(default=3, ge=1, le=100)
    buyer_budget: str = "10.00"
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))


class ExperimentResponse(BaseModel):
    rounds: int
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
                buyers=[BuyerAgent(name=f"buyer-{n}", budget=Decimal(request.buyer_budget))
                        for n in range(request.buyers)],
                rounds=request.rounds,
                model=request.model,
            )
            purchases = experiment.run()
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return ExperimentResponse(
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
                    buyers=[BuyerAgent(name=f"buyer-{n}", budget=Decimal(request.buyer_budget))
                            for n in range(request.buyers)],
                    rounds=request.rounds,
                    model=request.model,
                )
                events.put(("status", {"message": "The sellers are getting ready."}))
                purchases = experiment.run(
                    on_turn=lambda turn: events.put(("turn", turn.model_dump(mode="json"))),
                    on_round=lambda round_result: events.put(("round", round_result.model_dump(mode="json"))),
                )
                result = ExperimentResponse(
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

    return app


app = create_app()