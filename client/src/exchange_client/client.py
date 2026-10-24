"""Async client for independently running trading agents."""

import asyncio
import math
from collections.abc import AsyncIterator
from typing import Self
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import TypeAdapter
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .errors import ExchangeError, InvalidOrder, OrderNotFound, OrderNotOpen
from .models import (
    CancelResult, FairPrice, MarketSnapshot, Order, OrderRequest, OrderResult, Side,
)


class ExchangeClient:
    def __init__(
        self, agent_id: str, base_url: str = "http://127.0.0.1:8000", *,
        timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 128:
            raise ValueError("agent_id must contain 1–128 characters and not be blank")
        url = urlsplit(base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment:
            raise ValueError("base_url must be an http(s) URL without a query or fragment")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(
            base_url=self.base_url + "/", timeout=timeout, transport=transport,
        )
        self._ws_url = urlunsplit((
            "wss" if url.scheme == "https" else "ws", url.netloc,
            url.path.rstrip("/") + "/ws", "", "",
        ))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP connection pool. Close active stream iterators separately."""
        await self.http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self.http.request(method, path, **kwargs)
        if response.is_error:
            error = {404: OrderNotFound, 409: OrderNotOpen, 422: InvalidOrder}.get(
                response.status_code, ExchangeError,
            )
            raise error(response)
        response.raise_for_status()
        return response

    async def place_order(self, side: Side, price: int, quantity: int) -> OrderResult:
        """Submit a GTC limit order. Never automatically retries an order request."""
        order = OrderRequest(agent_id=self.agent_id, side=side, price=price, quantity=quantity)
        response = await self._request("POST", "orders", json=order.model_dump())
        return OrderResult.model_validate(response.json())

    async def buy(self, price: int, quantity: int) -> OrderResult:
        return await self.place_order("buy", price, quantity)

    async def sell(self, price: int, quantity: int) -> OrderResult:
        return await self.place_order("sell", price, quantity)

    async def cancel_order(self, order_id: str) -> CancelResult:
        if not order_id or order_id in (".", ".."):
            raise ValueError("order_id must be a nonempty order identifier")
        response = await self._request(
            "DELETE", f"orders/{quote(order_id, safe='')}", params={"agent_id": self.agent_id},
        )
        return CancelResult.model_validate(response.json())

    async def orders(self) -> list[Order]:
        response = await self._request("GET", "orders", params={"agent_id": self.agent_id})
        return TypeAdapter(list[Order]).validate_python(response.json()["orders"])

    async def snapshot(self) -> MarketSnapshot:
        response = await self._request("GET", "book")
        return MarketSnapshot.model_validate(response.json())

    async def fair(self) -> FairPrice:
        response = await self._request("GET", "fair")
        return FairPrice.model_validate(response.json())

    async def stream(
        self, *, reconnect: bool = False, max_retries: int = 5, retry_delay: float = 0.25,
    ) -> AsyncIterator[MarketSnapshot]:
        """Yield validated full snapshots, optionally reconnecting with backoff.

        Retry count resets after receiving a snapshot. Each reconnect receives
        fresh state; intermediate events can be missed. Invalid data propagates.
        """
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        if not math.isfinite(retry_delay) or retry_delay <= 0:
            raise ValueError("retry_delay must be finite and positive")
        retries = 0
        while True:
            try:
                async with connect(self._ws_url) as websocket:
                    async for message in websocket:
                        snapshot = MarketSnapshot.model_validate_json(message)
                        retries = 0
                        yield snapshot
                if not reconnect:
                    return
                if retries >= max_retries:
                    raise ConnectionError("Exchange stream reconnect limit reached")
            except (OSError, ConnectionClosed, InvalidStatus) as exc:
                if isinstance(exc, InvalidStatus) and exc.response.status_code not in (429, 500, 502, 503, 504):
                    raise
                if not reconnect or retries >= max_retries:
                    raise
            await asyncio.sleep(min(retry_delay * 2**min(retries, 16), 30.0))
            retries += 1
