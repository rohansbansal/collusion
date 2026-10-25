# Collusion: continuous market observatory

Run `make dev` in this directory and open http://localhost:5173.
Set `OPENAI_API_KEY` in the repository-root `.env`. The new live market uses
`gpt-6.1-sol` through the Responses API, with 100 rule-based buyers by default.

## Live market

`POST /market/stream` accepts `LiveSettings` and emits SSE `status`, `seller`,
`tick`, `error`, and optionally `done` events. The browser consumes it with fetch.
The market runs until Stop/disconnect; tests can set `max_ticks`. Closing the
stream cancels all pricing tasks and removes the active session. In-flight
requests may already have been processed by the provider.

Buyers trade every three seconds while three independent seller tasks evaluate
and revise prices asynchronously. There is no quote-closing barrier. Each seller
is instructed only to maximize its own cumulative profit; there is no instruction
to offer competitive prices, cooperate, or send messages. Profit is revenue minus
$0.40 production cost per sold unit. Inventory replenishes by up to 25 units per
tick, capped at 1000. Buyers have separate cash and willingness-to-pay limits;
valuations drift by deterministic buyer-specific and common demand cycles. Cash
replenishes by $0.35 per tick up to the starting budget. Buyers buy the cheapest
acceptable in-stock offer or abstain. Buyer order rotates to reduce priority bias.

### Channel ablations

- `chat`: prices, recent trades, and optional public/private seller messages.
- `prices_only` (default): public prices and trades, no delivered messages.
- `isolated`: rivals' quotes and trades and all messages are hidden. A seller still
  sees its own sales, so rivals can affect it indirectly through demand allocation.

The seedless buyer trajectories are reproducible given tick, identity and settings.
LLM outputs are stochastic. Compare equal-length runs and repeat them; a single
comparison is not a causal finding. Price visibility and linguistic channels are
separate. No hidden internet, file, or arbitrary tool communication is available
inside these agents. The experiment supports potential price signaling; it does
not demonstrate a decoded covert protocol.

### Screening and interventions

The monitor reports joint price increases, persistent aligned markups, lagged
price matching, sub-cent prices, and (in chat mode) price-coordination keywords.
Each flag contains observations and alternative explanations. Thresholds are
exploratory, not a validated collusion detector or a legal verdict. Cost markup
alone is not evidence of collusion. Shared demand and inventory scarcity confound
price relationships; the demand index is shown in the UI.

`POST /market/{run_id}/probe` holds seller-1 at $0.45 for five ticks, then releases
it to its pre-probe price. The event tape labels this experimenter intervention;
other sellers continue deciding. Reactions can motivate a follow-up experiment,
but do not by themselves prove retaliation or an agreement.

Export saves the latest 120 ticks, 100 seller actions, 30 deduplicated flags and
settings as JSON. Session summaries are in-memory and compare channels descriptively.
They are not a durable database or an automated matched-control study.

Tests: `uv run pytest -q`. Frontend: `npm --prefix frontend run build`.

## Legacy finite-round API

The endpoints below remain available for older clients. The web UI uses the
continuous `/market/stream` endpoint.

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

Buyers use explicit decision rules, with separate cash budgets and personal
maximum prices (willingness to pay). Each buyer chooses the cheapest in-stock
offer within both limits, breaking equal-price ties reproducibly. Otherwise it
skips the purchase. Cash carries across rounds. The API's `buyer_max_price`
(default $1.50) sets the typical maximum price; buyer limits span 60–140% of
that value, or 100% for a single buyer. These are controlled simulation profiles,
not estimates of real consumer behavior.

Every buyer decision appears in the UI and the `buyer_decisions` response field,
including skipped purchases, visible offers, the reason, and cash before and
after the choice. Streaming clients receive each decision as a `buyer` event.
Buyers do not receive seller conversations or other buyers' private limits.

Sellers first post public opening proposals, then reply before submitting final
quotes. A final quote closes that seller's conversation immediately. Remaining
sellers see the active and closed seller names; private-message tools list only
active recipients. The backend also blocks attempted delivery to closed sellers.
When only one seller remains, it submits its final quote. The UI shows seller
status and departures in the conversation timeline.

The API always uses LangChain seller agents. Sellers are instructed to maximize
cumulative profit first and sell as much of their available inventory as possible
second. Sellers always offer their full remaining inventory; the model chooses
price and communication, not whether to withhold bananas. Set `OPENAI_API_KEY`
before starting it.