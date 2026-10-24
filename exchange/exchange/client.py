"""Async agent API; use one client per agent inside an async context manager."""

import json
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect


class ExchangeClient:
    def __init__(self, agent_id: str, base_url: str = "http://127.0.0.1:8000"):
        if not agent_id.strip():
            raise ValueError("agent_id must not be blank")
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def place_order(self, side: str, price: int, quantity: int) -> dict:
        response = await self.http.post("/orders", json={
            "agent_id": self.agent_id, "side": side, "price": price, "quantity": quantity,
        })
        response.raise_for_status()
        return response.json()

    async def cancel_order(self, order_id: str) -> dict:
        response = await self.http.delete(f"/orders/{order_id}", params={"agent_id": self.agent_id})
        response.raise_for_status()
        return response.json()

    async def orders(self) -> list[dict]:
        response = await self.http.get("/orders", params={"agent_id": self.agent_id})
        response.raise_for_status()
        return response.json()["orders"]

    async def snapshot(self) -> dict:
        response = await self.http.get("/book")
        response.raise_for_status()
        return response.json()

    async def stream(self):
        """Yield full book/fair snapshots; failures propagate to the agent."""
        url = urlsplit(self.base_url)
        ws_url = urlunsplit(("wss" if url.scheme == "https" else "ws", url.netloc,
                            url.path + "/ws", "", ""))
        async with connect(ws_url) as websocket:
            async for message in websocket:
                yield json.loads(message)
