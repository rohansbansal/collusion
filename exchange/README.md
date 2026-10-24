# Banana market

This package is a small, in-memory market with three banana sellers. Each seller
publishes a price and has a limited inventory. `SellerAgent` represents each
seller, while `BuyerAgent` is a deliberately simple buyer. Create as many buyer
instances as needed and have them buy from the market.
All market, buyer, seller, and purchase objects are Pydantic models, so their
inputs are validated when they are created.

There is no external market connection or background simulation.

## Run

```sh
uv run python -m backend.main
```

The example prints the three quotes and has three agents buy one banana each.

## Use

```python
from decimal import Decimal

from backend import BuyerAgent, Market

market = Market()
agents = [BuyerAgent(f"buyer-{n}", Decimal("10.00")) for n in range(3)]

for agent in agents:
    purchase = agent.buy(market)
    print(purchase)
```

An agent always buys from the cheapest seller with enough inventory. Prices are
`Decimal` values. Each seller starts with 1,000 bananas and a $50 budget.
Inventory is not replenished: it decreases as bananas are sold across the entire
experiment.

Run the tests with `uv run pytest`.

## Communication experiments

Sellers can post either public messages to every seller or private messages to
one specific seller. Sellers are prompted to maximize their own profit and can
see only their own state, public market activity, and messages addressed to them.

```python
from decimal import Decimal

from backend import BuyerAgent, Experiment

experiment = Experiment(
    buyers=[BuyerAgent("buyer", Decimal("10.00"))],
    rounds=10,
)
experiment.run()
```

Use the same experiment settings repeatedly to compare prices, sales, revenue,
and the public and private conversations.

## API

The simplest way to start both the backend and frontend is:

```sh
cd exchange
make dev
```

This starts FastAPI and Vite together. Open the frontend URL printed by Vite,
normally `http://localhost:5173/`. Press `Ctrl+C` once to stop both processes.

Start the API with:

```sh
uv run uvicorn backend.api:app --reload
```

The backend loads `.env` from the repository root or the `exchange` directory.
It uses `OPENAI_API_KEY` for authentication and `OPENAI_MODEL` for the seller
model. Verify the configuration with:

```sh
uv run python -m backend.test_api_key
```

Start the Svelte frontend in a second terminal. Vite proxies API requests to the
backend, so the frontend should use relative API URLs and does not need to know
the backend port:

```sh
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite, normally `http://localhost:5173/`. Do not open
the backend URL as the frontend; `http://127.0.0.1:8000/` is only the API.

### What is a round?

One round is one complete market cycle:

1. Each seller receives its own state, public quotes, recent sales, and messages
   from earlier rounds.
2. Sellers exchange messages until each seller signals that it is ready to
   quote a price. Each turn lets them send a message and receive messages from
   the previous turn.
3. After the conversation, sellers choose final prices and quantities. These
   decisions are applied simultaneously.
4. Each buyer attempts to purchase one banana from the cheapest available quote.
5. Inventory, revenue, purchases, and messages are recorded.

The `rounds` setting repeats this cycle. Seller inventory carries from one round
to the next and is reduced by completed sales. A seller's `end_conversation` decision
is the structured equivalent of an end-conversation tool call. The conversation
continues until every seller has ended it. A seller's quantity decision is the
amount it offers during that round; its underlying inventory is only reduced by
completed sales.

### Run flow

The frontend follows this flow:

1. **Observe** — each seller sees its own state, public quotes, recent sales,
   and messages it is allowed to receive.
2. **Talk** — sellers exchange private or public messages until each seller
   signals that it is ready to quote a price.
3. **Quote** — sellers choose their final price and quantity simultaneously.
4. **Buy** — each buyer attempts to buy one banana from one available seller.
5. **Record** — the experiment saves trades, revenue, seller decisions, and all
   public and private communication for the response.

Run an experiment with:

```sh
curl -X POST http://127.0.0.1:8000/run_experiment \
  -H 'content-type: application/json' \
  -d '{"rounds":10,"buyers":3}'
```

Buyers do not have assigned seller preferences. Each buyer independently checks
what it can afford, and the market uses a stable buyer-specific selection to
spread otherwise identical demand across affordable sellers. Their budgets still
act as private willingness-to-pay limits. Each buyer purchases one banana per
round.

The API always uses LangChain seller agents. Sellers are instructed to maximize
cumulative profit first and sell as much of their available inventory as possible
second. Sellers always offer their full remaining inventory; the model chooses
price and communication, not whether to withhold bananas. Set `OPENAI_API_KEY`
before starting it.