"""API errors retain the HTTP response for programmatic inspection."""

import httpx


class ExchangeError(httpx.HTTPStatusError):
    def __init__(self, response: httpx.Response):
        self.status_code = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        self.detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        super().__init__(
            f"Exchange returned {self.status_code}: {self.detail}",
            request=response.request, response=response,
        )


class OrderNotFound(ExchangeError):
    """No order exists for this agent (HTTP 404)."""


class OrderNotOpen(ExchangeError):
    """The order is already filled or cancelled (HTTP 409)."""


class InvalidOrder(ExchangeError):
    """The server rejected request validation (HTTP 422)."""
