.PHONY: dev install test

dev: install
	@uv run uvicorn backend.api:app --reload & backend_pid=$$!; \
	trap 'kill $$backend_pid 2>/dev/null || true' INT TERM EXIT; \
	npm --prefix frontend run dev

install:
	uv sync
	npm --prefix frontend install

test:
	uv run pytest