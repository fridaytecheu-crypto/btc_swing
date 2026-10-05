#!/usr/bin/env bash
# Quality gates to run before any commit.
set -euo pipefail
uv run ruff format --check .
uv run ruff check .
uv run mypy btc_swing
uv run pytest -q
