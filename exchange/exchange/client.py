"""Compatibility facade for the original dictionary-based exchange client.

New agents should import the typed SDK directly from ``exchange_client``.
"""

from contextlib import aclosing

from exchange_client import ExchangeClient as TypedClient


class ExchangeClient:
    def __init__(self, agent_id: str, base_url: str = "http://127.0.0.1:8000"):
        self._client = TypedClient(agent_id, base_url)
        self.agent_id = self._client.agent_id
        self.base_url = self._client.base_url
        self.http = self._client.http

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    async def place_order(self, side: str, price: int, quantity: int) -> dict:
        return (await self._client.place_order(side, price, quantity)).model_dump(mode="json")

    async def cancel_order(self, order_id: str) -> dict:
        return (await self._client.cancel_order(order_id)).model_dump(mode="json")

    async def orders(self) -> list[dict]:
        return [order.model_dump(mode="json") for order in await self._client.orders()]

    async def snapshot(self) -> dict:
        return (await self._client.snapshot()).model_dump(mode="json")

    async def stream(self):
        async with aclosing(self._client.stream()) as feed:
            async for snapshot in feed:
                yield snapshot.model_dump(mode="json")
