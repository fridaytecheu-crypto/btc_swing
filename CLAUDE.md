# btc_swing — working notes for Claude sessions

Project: BTC Leveraged Swing Engine V1, a deterministic, point-in-time research engine for a
risk-controlled BTC perpetual LONG/SHORT swing methodology. Paper/backtest only.
Read `docs/HANDOFF.md`, then `docs/BTC_SWING_V1_DESIGN.md`,
`reports/BTC_SWING_V1_PHASE2_VALIDATION.md`, `reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md` and
`reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`,
`reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md` and `reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md`.

## Non-negotiables (owner's specification)
- No live trading, no exchange API keys, no order placement, no real money.
- No parameter search/optimisation; defaults are pre-registered. A rule change needs owner approval
  and a single pre-registered run.
- PIT discipline: a decision at T may only use bars with `close_time <= T` and auxiliary rows with
  `time + latency <= T`. Raw archive files are immutable (sha256 verified).
- 2025+ data is reserved for one confirmatory run; never use it for development.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.

## Environment
- Python 3.12+, `uv sync --all-extras`. No database. `BTC_DATA_DIR` (default ./data, git-ignored).
- Network must allow `data.binance.vision` and `s3-ap-northeast-1.amazonaws.com` (archive listing).
  Binance REST (`api/fapi.binance.com`) is geo-blocked from the cloud container and is not used.
- Checks before any commit: `bash scripts/check.sh` (ruff format/check, strict mypy, pytest).
- Work on branch `main` unless told otherwise.

## Layout
See README.md. Config `config/btc_swing.default.yaml`; CLI `btc-swing` (`data probe`,
`data ingest`, `backtest`, `phase2`, `phase21`, `phase22`, `phase23`, `phase24`); tests `tests/`; reproducibility manifests in `manifests/`.
