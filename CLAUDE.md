# btc_swing — working notes for Claude sessions

Project: BTC Leveraged Swing Engine V1, a deterministic, point-in-time research engine for a
risk-controlled BTC perpetual LONG/SHORT swing methodology. Paper/backtest only.
Read `docs/HANDOFF.md`, then `docs/BTC_SWING_V1_DESIGN.md`,
`reports/BTC_SWING_V1_PHASE2_VALIDATION.md`, `reports/BTC_SWING_V1_PHASE2_1_ENTRY_MECHANICS.md` and
`reports/BTC_SWING_V1_PHASE2_2_EARLY_ENTRY_CONFIRMATION_EXIT.md`,
`reports/BTC_SWING_V1_PHASE2_3_POST_TP1_EXIT.md`, `reports/BTC_SWING_V1_PHASE2_4_SHORT_REGIME.md` and
`reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md` (Phase 3: C, failed out of sample).
V1 is closed and immutable. V2 (learned ranking, `btc_swing/v2/`): `docs/BTC_SWING_V2_DESIGN.md`
and `reports/BTC_SWING_V2_RANKING_RESEARCH.md` (classification C — no useful ranking edge).
V3 (active multi-timeframe swing, `btc_swing/v3/`): `docs/BTC_SWING_V3_DESIGN.md` and
`reports/BTC_SWING_V3_ACTIVE_SWING_RESEARCH.md` (classification C — no robust structural edge).
V4 (event & positioning driven, `btc_swing/v4/`): `docs/BTC_SWING_V4_DESIGN.md` and
`reports/BTC_SWING_V4_EVENT_POSITIONING_RESEARCH.md` (classification C — no robust event edge).
V5 (microstructure & liquidation driven, `btc_swing/v5/`): `docs/BTC_SWING_V5_DESIGN.md` and
`reports/BTC_SWING_V5_MICROSTRUCTURE_RESEARCH.md` (classification C — no robust microstructure edge).
`btc_swing/v5/collector.py` is a PUBLIC-data Bybit collector (no auth); `btc_swing/v5/execution.py`
is a Bybit Demo execution abstraction that is designed but NOT activated (never activate it inside a
research phase; no credentials exist or are required).

## Non-negotiables (owner's specification)
- No live trading, no exchange API keys, no order placement, no real money.
- No parameter search/optimisation; defaults are pre-registered. A rule change needs owner approval
  and a single pre-registered run.
- PIT discipline: a decision at T may only use bars with `close_time <= T` and auxiliary rows with
  `time + latency <= T`. Raw archive files are immutable (sha256 verified).
- 2025-01..2026-09 was the single confirmatory holdout (Phase 3) and is now spent; it must not be
  used for development, and any new hypothesis needs a new owner pre-registration and a new window.
- Hard limits in the config schema: `max_leverage <= 10`, `risk_per_trade <= 2%`.

## Environment
- Python 3.12+, `uv sync --all-extras`. No database. `BTC_DATA_DIR` (default ./data, git-ignored).
- Network must allow `data.binance.vision`, `s3-ap-northeast-1.amazonaws.com` (archive listing) and
  `stream.bybit.com` (V5 public WebSocket collector).
  Binance REST (`api/fapi.binance.com`) is geo-blocked from the cloud container and is not used.
- Checks before any commit: `bash scripts/check.sh` (ruff format/check, strict mypy, pytest).
- Work on branch `main` unless told otherwise.

## Layout
See README.md. Config `config/btc_swing.default.yaml` (V1, frozen) and `config/btc_swing_v2.default.yaml`
(V2 protocol), `config/btc_swing_v3.yaml` (V3, frozen), `config/btc_swing_v4.yaml` (V4, frozen),
`config/btc_swing_v5.yaml` (V5, frozen); CLI
`btc-swing` (`data probe`, `data ingest`, `backtest`, `phase2`, `phase21`, `phase22`, `phase23`,
`phase24`, `phase3-freeze`, `phase3`, `v2 research`, `v3 research`, `v4 research`, `v5 ingest`,
`v5 collect`, `v5 research`); tests `tests/` (+ `tests/v3/`, `tests/v4/`, `tests/v5/`); reproducibility manifests in
`manifests/`. Never modify a closed generation's modules, configs or reports for later work.
