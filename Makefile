install:
	uv sync --extra dev

seed:
	uv run python scripts/seed_data.py

run:
	uv run uvicorn app.api.main:app --reload

mcp:
	uv run python mcp_server/server.py

worker:
	uv run python -m app.cli outbox

test:
	uv run pytest -q

lint:
	uv run ruff check app src tests scripts mcp_server

format-check:
	uv run ruff format --check app src tests scripts mcp_server

typecheck:
	uv run mypy app src

eval:
	uv run python -m app.eval.run_eval

deep-eval:
	uv run python -m app.eval.deepeval_suite

demo:
	uv run python scripts/run_demo.py

phoenix:
	.venv-phoenix/bin/phoenix serve

traces:
	.venv-phoenix/bin/python scripts/export_traces.py

evidence:
	uv run python scripts/generate_evidence.py

smoke:
	./scripts/run_smoke.sh

check:
	uv run ruff check app src tests scripts mcp_server
	uv run pytest -q
