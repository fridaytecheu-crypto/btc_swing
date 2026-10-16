# BTC V11-LITE-WARM — warm-started V11 shadow with the A/B/C family structure as labels (pre-registered)

Status: built and pre-registered 2026-10-14, BEFORE any observation. SHADOW ONLY: no exchange client, no Demo
authority, no order placement capability exists in `btc_swing/v11_lite_warm/`. V10 (Demo), the frozen V11
shadow and V11-WARM keep running unchanged; V11-LITE-WARM never reads or writes their roots, freezes, journals
or counters (proved by hash tests). V11-WARM is the strict CONTROL; V11-LITE-WARM is the relaxed CHALLENGER.

## 1. Purpose

V11-WARM has shown ~24 candidates and 0 armed states in its first day: the A/B/C family structure conditions
(impulse-pullback + 15m structure + turn; compression + near boundary; sweep + RSI exhaustion) reject every
high-score minute. V11-LITE-WARM tests whether the V11 multi-factor score plus the live microstructure
contribution has forward predictive value WITHOUT those structure gates. The main experiment is the
INCREMENTAL cohort: the signals LITE takes that the strict V11 rules would reject.

## 2. The ONE strategy delta (exact)

`V11LiteEngine(V11Engine)` overrides a single method:

```
def structure(self, fam, s, i):           # V11-LITE
    c, a5 = X["close"][i], X["atr5"][i]
    return isfinite(c) and isfinite(a5) and a5 > 0     # data validity only (V11's own first guard)
```

The frozen `V11Engine.structure` is still executed for every family and side on every bar and journaled as
`structure_labels` on every CANDIDATE, ARMED, NEAR_MISS and SIGNAL record (field names as of the section 8
cleanup); it never vetoes arming. Consequence: a (family, side) key arms whenever the side's score is >= the
armed threshold (70) and the family's own trigger and stop geometry then decide the entry exactly as in V11
(`trigger`, `stop_level`, `_decide`, `_manage`, `_close` are inherited untouched). The family of a LITE
signal is therefore its trigger / stop TEMPLATE (A: 3-bar breakout, pullback/swing stop; B: 60-min
boundary breakout with rvol >= 1.2 and extension <= 0.5 ATR, level stop; C: reclaim of the swept level,
sweep-extreme stop), evaluated in the frozen order A, B, C per minute, one decision per minute.

Kept byte-identical (V11's own config object, hash-checked against `manifests/v11_forward_freeze.json`,
V11 strategy hash `a324dbcb0394…`): feature set `v11-feat-1`, score model `v11-score-1` and weights,
contributions, live microstructure contribution (V10 feature-engine instance owned by this generation),
candidate 65 / armed 70 / entry 75, stop floor 0.35% / cap 1.00%, TP1 +0.6R 40% / TP2 +1.2R 40% / runner
trail / break-even / 20-min stall review / 60-min hard stop, risk 0.15% = 7.5 USDT, leverage ladder, costs,
warm-start (same bootstrap bars, 31-day window, 30-min live warm-up), seam healer, derivatives inputs.
Signal ids use the prefix `V11LWS-` (no `V11LWD-` exists). `outcomes.horizons_min` is [5, 15, 30, 60, 120]:
`outcomes` is not a V11 strategy section, so the V11 strategy hash is unchanged (asserted at load).
Nothing else is changed; no threshold is lowered; no additional hard filter is added.

## 3. Read-only context telemetry (no decision reads it)

Computed once per completed 1m bar from bars <= the decision bar (PIT), journaled on every CANDIDATE, ARMED,
NEAR_MISS and SIGNAL record and carried into the shadow trade's `decision`:

- Fibonacci: the last confirmed swing leg on completed 5m bars (pivot = high above / low below the k = 2
  bars on each side, confirmed only after 2 later 5m bars closed; search window 24 h). The leg ENDS at the
  newest confirmed pivot and STARTS at the opposite extreme since the previous pivot of the same type. Output:
  swing high / low and their rows, direction (UP = leg into a swing high), leg size in bps, retrace fraction,
  38.2 / 50 / 61.8% levels, distance close -> level in bps and in ATR5, nearest level, |distance| / ATR5
  bucket (<= 0.10 / 0.10-0.25 / 0.25-0.50 / > 0.50). `available: false` (N/A) when no confirmed pivot exists.
- VWAP: level, distance in bps and in ATR5, 15-min slope in ATR5.
- Range position: (close - low) / (high - low) over the last 60 and 240 completed bars, with width in bps
  (N/A when fewer than 60 / 240 bars or > 5% missing).
- Swing distances: close -> swing_hi5 / swing_lo5 / swing_hi15 / swing_lo15 / hi60 / lo60 (bps, ATR5).
- ATR-normalised location: close - EMA21, close - EMA50, close - VWAP, distance to hi60 / lo60 (ATR5).
- Flow: delta1 / delta5 / delta15, imb5 / imb15, intensity1, rvol5 (the frame's own columns).
- Microstructure / order book / liquidation context: the V10-engine features of the last row (depth
  imbalance, bid / ask replenishment, spread, liquidation burst percentile and imbalance, taker imbalance
  10 / 30 / 60 s, delta 30 s, intensity percentile, efficiency, absorption), as already journaled by V11.
- `structure_detail`: the frozen V11 structure sub-conditions per family and side (A: impulse_pullback,
  struct15_aligned, turn; B: compression, near_boundary; C: sweep_recent, rsi_exhausted) so each
  rejection names the failed condition.

## 4. The in-process strict control twin

`V11LiteWarmLive` runs an unchanged `V11Engine` ("twin") on the identical frame, scores and microstructure
arrays every minute, with its own position / armed state and its own hash-chained journal
`journals/control_twin.jsonl` (TWIN_EXECUTED / TWIN_BLOCKED / TWIN_OPEN / TWIN_CLOSED). It never influences
a LITE decision. Each LITE signal carries `control_twin`: `would_take` (the twin decided the same side this
minute), `reason` (`CONTROL_SAME_DECISION`, `CONTROL_SAME_SIDE_OTHER_FAMILY:<F>`,
`STRUCTURE_<F>_FALSE:<failed conditions>`, `CONTROL_ARMED_<F>_NO_DECISION`,
`STRUCTURE_<F>_TRUE_NOW_BUT_CONTROL_NOT_ARMED`, with `|CONTROL_BLOCKED_POSITION_OPEN` /
`|CONTROL_DECIDED_OTHER_SIDE:<F><side>` suffixes), the structure detail and `twin_decision`.
`incremental = not would_take`. The authoritative control numbers remain V11-WARM's own journals (separate
process, separate feed); the twin is the per-event attribution on identical inputs.

## 5. Comparison report (read-only)

`btc-swing v11lw report compare` reads the V11-WARM and V11-LITE-WARM journals through the generic
`GenerationSpec` layer and writes `compare_<ts>.{json,md}` + `latest_compare.*` under
`reports/forward_v11_lite_warm/` only: candidates / armed records / armed STARTS (a record whose
family-side had no ARMED record in the previous minute; V11 journals ARMED on every re-arm minute) /
triggers counter / executed / blocked / shadow trades, all per observed day; conversions; L / S; families;
score distributions; gross / net R, PF, win rate, max drawdown (USDT and R), median hold, exit reasons;
side-adjusted mean returns at 5 / 15 / 30 / 60 / 120 min by kind; the INCREMENTAL COHORT table (timestamp,
side, template, score, A/B/C labels, exact control rejection reason, microstructure sub-score, returns at
every horizon, final net R, Fibonacci ratio / distance / bucket, VWAP distance, 1h / 4h range position, 5m
swing distances) with its summary (n, net R mean and one-sided 90% upper confidence bound, returns by
horizon, reasons, sides); the Fibonacci attribution table per bucket (count, 30m and 60m side-adjusted
return, closed-trade net R) for executed signals and candidates; the checkpoints. Every cell with fewer than
30 samples is labelled PRELIMINARY. No threshold is derived from any table.

## 6. Pre-registered checkpoints (mechanical; no forward tuning)

| checkpoint | reached when ANY | requirement to continue |
|---|---|---|
| 1 | 7 fully-warm days OR 300 candidates | inspection only: funnel, label distribution, telemetry present |
| 2 | 30 closed shadow trades OR 21 fully-warm days | gross R / trade > 0 AND incremental cohort not clearly negative (one-sided 90% upper confidence bound of its closed-trade net R > 0) |
| 3 | 60 closed shadow trades OR 45 fully-warm days | net R / trade > 0, PF > 1.1, max drawdown <= 15 R (112.5 USDT at 7.5 USDT risk), no side > 80% of a positive total net R, no single UTC day > 50% of a positive total net R |

"Fully-warm days" = days since observation_start (every bar-derived group is warm from the first bar by
construction; the microstructure group after the 30-min warm-up). A failed checkpoint STOPS the experiment
(the generation stays frozen as a record). Passing checkpoint 3 does NOT activate anything: any Demo
execution needs a new owner decision and a new pre-registration. No rule, threshold or telemetry definition
is changed during the observation.

## 7. Isolation, freeze, commands

Package `btc_swing/v11_lite_warm/` (config, paths, engine, context, live, runner, freeze, status, compare,
cli), config `config/btc_swing_v11_lite_warm.yaml`, data `<BTC_DATA_DIR>/btc/forward_v11_lite_warm/`
(verification `forward_v11_lite_warm_verify/`), reports `reports/forward_v11_lite_warm/`, freeze
`manifests/v11_lite_warm_forward_freeze.json` (pins the V11 strategy hash, the lite strategy hash =
sha256(V11 strategy hash + "labels_only"), the lite config hash, bootstrap content hash, warm-start
parameters, outcome horizons, checkpoints, code commit, observation_start = frozen_at + 30 min), macOS agent
`com.btcswing.v11-lite-warm-forward` (`deploy/macos/install_v11_lite_warm_macos.sh` installs ONLY this
agent and seeds the root first). CLI: `btc-swing v11lw config|seed`, `v11lw forward
run|status|diagnostic|verify-warm|shadow-report|freeze|trades`, `v11lw report full-history|compare`.
Tests (`tests/v11_lite_warm/`): the LITE engine with its one override reverted reproduces the frozen V11
engine trade for trade; labels equal `V11Engine.structure` on every row; LITE arming is a superset of V11's
while both are flat; trigger and stop functions are identical; the live `_step` replay proves the twin
reproduces a standalone frozen V11 engine and that every incremental signal names the failed condition;
context telemetry is PIT (unchanged when later bars change) and N/A when unconfirmable; V10 / V11 / V11-WARM
code, configs, freezes and bootstrap hashes are unchanged by loading, seeding, running, freezing and
reporting LITE; nothing is written outside the LITE root and `reports/forward_v11_lite_warm/`.

## 8. Pre-launch bookkeeping cleanup (`v11lw-book-2`, 2026-10-14) — economically identical

A read-only design audit of the first build (code f885f85, freeze 2026-10-14T13:34:40Z, which never entered
Mac forward observation) found that the per-(family, side) arm keys inflated the journals: every refresh
minute wrote three ARMED records per side (one per template), a decision that consumed one template made
that template "re-arm" as a new start, near misses were written once per triggered template, and the
selected template was journaled under the name `family`. The owner ordered a bookkeeping-only cleanup with
the constraint that trade decisions stay bit-for-bit identical. Changes (`btc_swing/v11_lite_warm/engine.py`,
`live.py`, `compare.py`, `status.py`, `freeze.py`):

- ONE arm state per side (`V11LiteEngine.side_arm`): a refresh (data validity + score >= 70) re-arms all
  three frozen templates of that side at once; a template stays available until its frozen
  `armed_valid_min` lapses (A / B 30 min, C 10 min) or a decision consumes it. This is the f885f85 key
  dictionary written as one state per side; the decision loop evaluates the templates in the identical
  order (A LONG, A SHORT, B LONG, B SHORT, C LONG, C SHORT).
- An arm START (`arm_episode_id`, journal `ARMED`) is written only on the side's NOT_ARMED -> ARMED
  transition; refreshes and template consumption never create a new start.
- At most one ARMED, one decision, one NEAR_MISS per side-minute (the engine already made at most one
  decision per minute; near misses are now one record listing `templates_triggered`).
- Terminology: the template that fires first is journaled as `selected_template` (A / B / C trigger + stop
  template, NOT a setup); the frozen V11 structure results of that bar as `structure_labels` (A / B / C
  true / false). No LITE journal carries a `family` field. The control twin keeps V11's own `family`
  (there it IS a setup).
- Comparison report: unique candidates, side-level arm episodes (the control's per-family refresh records
  are merged into side episodes through their `valid_until` intervals), decisions per side-minute and per
  arm episode, blocked decisions per side-minute and blocked-only arm episodes, near misses per
  side-minute and per arm episode, trades; `selected_template` shown apart from
  `frozen_structure_label_A/B/C`; the incremental cohort's mean returns use EXECUTED decisions only.

Equivalence proof: `tests/v11_lite_warm/legacy_engine_f885f85.py` is the f885f85 engine verbatim (sha256
`b3291dec162a…`, asserted). Tests replay it and the cleaned engine on identical inputs (four synthetic
series, three adversarial series with random score walks across 65 / 70 / 75 and ATR5 blanked after arms
exist, which exercises the template validity windows and consumption, and the live `_step` path) and assert
every decision field, every trade field, candidates and counters equal, and that the side-level availability
equals the f885f85 key dictionary on every minute. A one-off replay on the consumed TRAIN and VALIDATION
frames (loads capped at 2025-07-01) compared every decision and trade field (result recorded in
`reports/forward_v11_lite_warm/equivalence_f885f85_vs_book2.json`; no performance is reported).
The economic strategy hash (`lite_strategy_hash` c66ef46a493f…) is unchanged; the new freeze pins
`bookkeeping_version: v11lw-book-2` and records the superseded freeze
(`manifests/v11_lite_warm_forward_freeze_superseded_f885f85.json`, byte-identical, sha256 814d6507…).

## 9. Bybit DEMO execution layer (2026-10-16) — execution only, strategy unchanged

Owner request: an optional Bybit DEMO (paper money) execution of the SAME frozen V11-LITE shadow decisions;
the shadow keeps recording every eligible trade and remains the source of truth. Full specification:
`docs/V11_LITE_WARM_DEMO_EXECUTION.md` (package `btc_swing/v11_lite_warm/demo/`, config
`config/btc_swing_v11_lite_warm_demo.yaml`, manifest `manifests/v11_lite_warm_demo_execution_freeze.json`).
The only change to the shadow modules is a copy-only, fire-and-forget observer hook after the shadow
journals each bar (`V11LiteWarmLive.demo_sink`) and a supervisor thread in the runner (observation root
only); the lite strategy hash, the bookkeeping version and the shadow freeze are unchanged, and tests prove
the shadow journals are identical with and without the Demo observer. Demo risk 0.10 % of 5000 USDT (the
shadow keeps its frozen 0.15 % virtual sizing), leverage 1, caps 10 / 24 h, daily stop -25 USDT, halt after 3
consecutive full losses, reconciliation on any mismatch; isolated Demo account by default; owner activation
only.
