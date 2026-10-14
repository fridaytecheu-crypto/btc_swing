# BTC V11-WARM — warm-started V11 shadow (operational experiment, not a strategy change)

Status: built 2026-10-13. SHADOW ONLY. V11-WARM runs the frozen V11 strategy (every strategy section is
V11's own config object, hash-checked against `manifests/v11_forward_freeze.json` at every load) with
ONE operational difference: a PIT-safe feature warm-start, so that the rolling baselines that the cold
V11 shadow needs about 21 days to fill (RVOL, ATR percentiles, EMA / ADX state) are available from the
first live bar. V10 (Demo) and the frozen V11 shadow keep running unchanged; V11-WARM never reads or
writes their roots, freezes, journals or counters, has no exchange client and no Demo authority.

## 1. Why

The V11 cold-start audit (2026-10-13) showed: RVOL needs more than 20 calendar days of bars in the live
frame (`slot_median_prev_days`), the 5m ATR percentile needs ~2.4 days, the 15m structure a few hours, and
the 1h ATR used by sizing ~14 h. The backtest had a 30-day warm-up, so the live scorer was not comparable
to the historical one for three weeks. Replay of the exact live code from a cold start reached 0.4
trades/day in the first 8 h and ~2.4/day only after a week (warm: 2.5/day).

## 2. Bootstrap (what is seeded, from where)

- `btc-swing v11w bootstrap` streams the Bybit PUBLIC trade archive (`public.bybit.com/trading/BTCUSDT/`,
  the same source and the same aggregation as the Bybit-native research dataset) for the 30 calendar days
  before a UTC day, builds exact 1m bars (open/high/low/close/volume/taker buy qty/taker sell qty/trade
  count), hashes and discards each raw file (1.72 GB streamed, nothing retained) and writes
  `manifests/v11_warm_bootstrap_1m.parquet` (1.1 MB, 43,200 bars, contiguous, content hash
  `3132d3410b54…`) plus `manifests/v11_warm_bootstrap_manifest.json` (per-day sha256, trade counts).
  Committed: every host seeds the identical bars; the freeze pins the content hash.
- Range: 2026-09-13 00:00 -> 2026-10-13 00:00 UTC, strictly before any observation_start. NOTE: the first
  18 days (2026-09-13..09-30) lie inside the locked historical holdout (2025-07..2026-09). They are used
  ONLY as rolling-baseline context for prospective decisions after observation_start; no statistic,
  decision, trade or parameter is derived from them, and they are never written into a research dataset
  (the research ingesters still refuse holdout days). The alternative (seed from 2026-10-01 only) would
  have left RVOL unavailable until ~2026-10-22.
- `btc-swing v11w seed` writes the bootstrap bars into the V11-WARM root as the V11 live bar format
  (`derived/bars_1m.jsonl`, flagged `src: bootstrap`) and refuses a root that already holds bars or any
  bar that would close after a known observation_start. It records `derived/seed.json`.
- Gap-fill (Mac only; the Bybit REST endpoint is geo-blocked in the cloud container): `seed` appends Bybit
  PUBLIC 1m klines from the archive end to the last completed minute (`src: kline_gapfill`, bounded by
  `gapfill_max_hours: 48`, never reaching observation_start). Price and volume are exact; the taker split
  is UNKNOWN and stored as NaN (not fabricated); the trade count is a validity placeholder (1). Effects are
  confined to the <= 20-minute flow windows (5/15-min imbalance and delta, 20-min intensity), which lie
  inside the live warm-up. Without gap-fill the archive->live gap simply warms live (5m ATR ~75 min, 60-min
  range ~61 min, 1h ATR ~14 h; RVOL, EMA, RSI, ADX, VWAP, structure immediately).
- Derivatives (OI, funding) warm live from the Bybit ticker (1-h OI change after 60 min). No PIT OI /
  funding history is reachable from the container; nothing is fabricated.
- NEVER backfilled: order book, replenishment / depletion, live taker-window microstructure,
  allLiquidation. The V10 feature engine instance owned by V11-WARM starts empty at runner start; its
  normalised features exist after 180 samples x 10 s = 30 min (the pre-declared live warm-up).

## 3. Runtime (what differs from the V11 live loop, and what does not)

`btc_swing/v11_warm/live.py` subclasses the V11 live scorer: same `build_frame_v11`, same
`score_frame`, same `V11Engine`, same journals, outcomes and near-miss logic. Differences:
(1) the rolling frame covers `window_days: 31` (V11: 22) so the bootstrap feeds every baseline;
(2) new completed bars are detected by their open time instead of the deque length (a full deque keeps a
constant length while it rolls; the V11 live loop compares lengths, which would stop its scoring once 22
days of live bars exist, i.e. around 2026-11-04 on the Mac: reported, NOT changed, owner's decision);
(3) `observation_start = frozen_at + live_warmup_min (30)`: the engine is in-window and candidates are
journaled only after it, so no decision uses the first 30 live minutes;
(4) the diagnostic carries a `warm` block and `verify-warm` reports the pre-declared field / group lists.
(5) **seam healing** (added 2026-10-14 after the first Mac install showed ATR5 / HI60 / tercile missing):
the kline gap-fill ends at the minute before `seed` ran, while the live bar builder's first bar is the
minute of the first trade after the agent connected, so one or two minutes were missing at the seam. A
single missing minute breaks every contiguous-window feature (`window_valid`): 5m ATR needs 14 valid
5m bars (75 min), HI60 61 valid minutes, Bollinger 20 valid 5m bars (100 min), and with ATR5 the trend,
momentum, volatility and location groups. The EMA / RSI / ADX / RVOL state carries across the hole, which
is why those looked warm. The runtime now (a) loads `bars_1m.jsonl` sorted and de-duplicated, (b) heals
every missing minute inside the window at startup and whenever a new live bar opens more than one minute
after the previous one (feed reconnects included) with Bybit PUBLIC klines flagged `src: kline_seamfill`
(same fields as gap-fill bars), bounded by `gapfill_max_hours` per hole, and (c) reports
`holes_in_window` and `seam_fills` in the diagnostic. Seam bars closed before the current live bar, so no
decision uses information from its own future. Where REST is unreachable (cloud container) the hole is
logged once and warms live, as before. Tests: `test_one_minute_seam_hole_reproduces_the_mac_symptom_without_healing`,
`test_seam_hole_is_healed_on_first_live_bar_and_first_decision_sees_all_bar_groups`,
`test_startup_heals_a_historical_seam_hole`, `test_reconnect_hole_mid_run_is_healed`.
`tests/v11_warm/test_v11_warm.py::test_identical_decisions_on_the_same_warmed_history` proves that V11
and V11-WARM, given the same fully warmed bar history, produce identical indicators, scores and decisions
bar by bar.

## 4. Freeze and PIT

`manifests/v11_warm_forward_freeze.json` pins: the V11 strategy hash (must equal the V11 freeze), the
bootstrap content hash / range / provenance, the gap-fill policy, `window_days`, `live_warmup_min`,
`frozen_at` and `observation_start`. The runner refuses to start without it, or if any pinned value
differs. Every statistic of V11-WARM starts at `observation_start`; bootstrap bars close before
`frozen_at` (asserted when freezing and when seeding).

## 5. Paths, commands, isolation

`btc_swing/v11_warm/`, `config/btc_swing_v11_warm.yaml`, data `<BTC_DATA_DIR>/btc/forward_v11_warm/`
(verification: `forward_v11_warm_verify/`), reports `reports/forward_v11_warm/`, macOS agent
`com.btcswing.v11-warm-forward` (`deploy/macos/install_v11_warm_macos.sh` installs only this agent and
seeds the root first). CLI `btc-swing v11w config|bootstrap|seed`, `v11w forward
run|status|diagnostic|verify-warm|shadow-report|freeze|trades`. The isolation test asserts no V10
execution / activation / paths import, no V11 freeze / runner / forward path, no exchange client, no
Demo token, and that loading, seeding and freezing V11-WARM leaves the V11 freeze and config byte-identical.
