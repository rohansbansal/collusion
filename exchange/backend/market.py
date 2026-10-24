"""A small in-memory banana market."""

from decimal import Decimal
from hashlib import sha256

from pydantic import BaseModel, Field, model_validator

class Sale(BaseModel):
    model_config = {"frozen": True}

    buyer: str
    seller: str
    product: str
    quantity: int
    price: Decimal
    total: Decimal


class Quote(BaseModel):
    seller: str
    product: str = "banana"
    price: Decimal = Field(gt=0)
    quantity: int = Field(ge=0)


class Purchase(Sale):
    buyer: str
    seller: str
    product: str = "banana"
    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    total: Decimal = Field(gt=0)


from .agents import SellerAgent


class Market(BaseModel):
    """A market with three banana sellers and a simple best-price checkout."""

    sellers: list[SellerAgent] = Field(default_factory=lambda: [
        SellerAgent(name="seller-1", price=Decimal("1.00")),
        SellerAgent(name="seller-2", price=Decimal("1.10")),
        SellerAgent(name="seller-3", price=Decimal("1.20")),
    ])
    purchases: list[Sale] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_three_sellers(self):
        if len(self.sellers) != 3:
            raise ValueError("the market must have exactly three sellers")
        return self

    def quotes(self) -> list[Quote]:
        return [seller.quote() for seller in self.sellers]

    def buy(
        self,
        buyer: str,
        quantity: int = 1,
        max_price: Decimal | None = None,
        randomize: bool = False,
    ) -> Sale:
        available = [
            seller for seller in self.sellers
            if seller.quote().quantity > 0
            and (max_price is None or seller.price <= max_price)
        ]
        if not available:
            raise ValueError("no affordable seller has enough bananas")
        if randomize:
            selection_key = f"{buyer}:{len(self.purchases)}".encode()
            seller = available[int.from_bytes(sha256(selection_key).digest()[:4], "big") % len(available)]
        else:
            seller = min(available, key=lambda item: (item.price, item.name))
        quantity = min(quantity, seller.quote().quantity)
        if max_price is not None:
            quantity = min(quantity, int(max_price / seller.price))
        if quantity <= 0:
            raise ValueError("buyer cannot afford a banana")
        total = seller.sell(quantity)
        purchase = Sale(
            buyer=buyer,
            seller=seller.name,
            product="banana",
            quantity=quantity,
            price=seller.price,
            total=total,
        )
        self.purchases.append(purchase)
        return purchase