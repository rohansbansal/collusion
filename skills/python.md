# Python modeling skill

Use Pydantic `BaseModel` classes for structured data whenever practical.

## Rules

- Define a Pydantic model for request bodies, responses, observations, decisions,
  messages, quotes, trades, configuration, and other domain objects.
- Add field constraints to the model instead of relying only on caller behavior.
- Return Pydantic models from application and API functions when the result has a
  known structure.
- Prefer nested Pydantic models over dictionaries containing mixed or implicit
  schemas.
- Use `list[SomeModel]` for collections of structured objects.
- Use dictionaries only when the keys are genuinely dynamic, such as a mapping
  from agent name to a numeric value. Type those maps explicitly, for example
  `dict[str, Decimal]`.
- Avoid loose `dict` type hints for domain data. If a dictionary has known keys,
  create a model for it instead.
- Use `TypedDict` only when a Pydantic model is unsuitable, such as a small
  internal, static mapping that does not need validation or serialization.

When adding a class, first ask whether it represents structured data. If it does,
make it a `BaseModel` by default. Use ordinary classes only for behavior-heavy
services whose state is not an external data shape.