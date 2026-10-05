#!/usr/bin/env bash
# Reproduce the Phase 2 validation from scratch: ingest the archive (resumable, checksum-verified),
# then run the frozen-default validation. Research only; no exchange credentials are used.
set -euo pipefail
export BTC_DATA_DIR="${BTC_DATA_DIR:-./data}"
uv run btc-swing data ingest --from 2021-10 --to 2024-12
uv run btc-swing phase2 --out "$BTC_DATA_DIR/btc/runs/phase2_validation"
echo "report: reports/BTC_SWING_V1_PHASE2_VALIDATION.md"
