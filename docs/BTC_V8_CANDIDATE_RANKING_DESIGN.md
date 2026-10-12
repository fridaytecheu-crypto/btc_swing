# BTC V8 — CANDIDATE RANKING: pre-registered design (SUPERSEDED 2026-10-12, never run)

Owner decision 2026-10-12: the ranking / ML direction was stopped before any V8 code or data read; the
next track is V9 (`docs/BTC_V9_SIMPLE_TREND_VOLUME_PULLBACK_DESIGN.md`). This document is kept on record only.

Status: DESIGN ONLY. No V8 code exists, no V8 data has been read, nothing has been run. This document
is the pre-registration that must be frozen (by commit hash) BEFORE any V8 harness touches data. V5,
V5.1, V6, V6.1 and V7 are closed and untouched; V8 will be isolated in `btc_swing/v8/` (config
`config/btc_swing_v8.yaml`, CLI `btc-swing v8 …`, report `reports/BTC_SWING_V8_CANDIDATE_RANKING_RESEARCH.md`,
tables `reports/v8/`). Paper research only: no exchange client, no credentials, no order path, nothing
activated, `V8D-` order-id prefix reserved and unused. The locked holdout 2025-07-01 -> 2026-09-30 is
neither downloaded nor read. No new data ingest: V8 runs on the existing V7 Bybit-native 1m dataset
(`data/btc/datasets/bybit_trades_1m/`, 1,324 days 2021-11-15 -> 2025-06-30, manifest hash recorded in
`manifests/v7_bybit_1m_dataset_manifest.json`) plus the existing Binance auxiliary series used by V6.1
(funding only, for cost accounting).

## 0. Why V8, and what it inherits

- V7 Phase 1 (`reports/BTC_SWING_V7_EDGE_DISCOVERY.md`) showed that microstructure STATES carry almost
  no incremental information (flow features within +-0.03 ATR5m, under 1 bp, of matched controls).
  Only two structural definitions survive both validation windows and they are small: FAILED_BREAK
  (~2 bps at 60 min, 25.8 first-in-cluster events/day) and E1 TREND_PULLBACK_RELEASE (~3 bps, 3.6/day;
  the +-1.0% bracket is the only one net positive after the ~15 bps round trip, about +6 bps).
- The original V7 Phase 2 (three fixed engines) is NOT built: a fixed-rule engine on a 2-3 bps mean
  effect cannot clear a 15 bps round trip at 3-6 trades/day.
- V8 asks a different question: not "is this state followed by displacement on average" but "within a
  PERMISSIVE population of deterministic candidates, does the PIT-safe context at the candidate
  separate the good ones from the bad ones strongly enough that the top slice is net positive at 3-6
  trades per day".
- Prior evidence against: V2 (`reports/BTC_SWING_V2_RANKING_RESEARCH.md`) ranked 838 multi-day V1
  swing candidates with 85 features and found no useful ranking edge (classification C). V8 differs in
  population size (10-30 candidates/day, ~9k-27k in TRAIN alone versus 838 in 4.5 years), horizon
  (30-180 min, so costs are the dominant term and must be modelled per candidate), feature lineage
  (the V7 frame, whose effects are measured) and in Stage A below: no model is fitted until a
  model-free check shows that ranking information exists. The V2 result is a prior that V8 may fail;
  the abandonment criteria in section 11 are written accordingly.

## 1. Exact hypothesis

H1 (primary, confirmatory). Let C be the deterministic candidate population of section 2 (direction-aware
intraday episodes, 10-30 per calendar day on TRAIN). Let s(c) be a PIT-safe score computed at the
candidate minute from the frozen feature set of section 4 by the frozen ranking method selected on
TRAIN (section 6-7). Then on each validation window (VALIDATION 1 and RECENT), with all of generator,
labels, features, score and cutoff frozen on TRAIN:

1. the net expectancy (after Bybit taker fees, slippage, spread and funding, section 8) of the primary
   label (section 5) is MONOTONE NON-INCREASING across the score buckets top 5% / top 10% / top 20% /
   top 30% / remaining 70%; and
2. the executed one-position-at-a-time simulation that trades only candidates above the TRAIN-selected
   cutoff has 3-6 executed trades per calendar day on average AND positive net expectancy per trade
   with a day-cluster bootstrap 95% confidence interval above zero on the pooled validation year, and
   positive net mean in each window individually; and
3. the ranking is not merely a volatility (cost-coverage) selection: at the same trade count the frozen
   score beats the cost-coverage-only control score S4 by at least 2 bps net per trade.

H0 (null). Conditional on being a candidate, future net outcome is independent of the context score:
bucket expectancies are equal within noise, the top slice is no better than the unranked population,
and a label-permutation null reproduces the observed top-slice expectancy. Under H0 V8 is abandoned
(section 11); candidate selection cannot be used to rescue 1-3 bps base effects.

Scope boundaries fixed now: BTCUSDT linear perpetual only; Bybit-native 1m data; holds 30 min to
3 h by construction of the labels; one position at a time; `max_leverage <= 10`, `risk_per_trade <=
2%` (V8 uses 0.15% as V6/V6.1); longs and shorts symmetric by definition (no direction-specific rules).

## 2. Candidate generators (deterministic, permissive, direction-aware)

All generators run on the V7 1m frame (`btc_swing.v7.features.build_frame_v7`, PIT by construction).
A candidate is emitted at the close of minute t with a direction d in {+1, -1}; the first eligible
entry is the open of minute t+1. Generators are permissive by design: they encode the LOCATION and
STRUCTURE of a possible trade, never its quality; quality is the ranking layer's job. Every generator
is first-in-cluster per direction with a 15-minute cluster (as V7), then the union is de-duplicated:
two candidates of the same direction within 15 minutes keep the earlier one; opposite directions are
both kept (the ranking decides).

| id | source (V7 evidence) | candidate rule (all PIT at the 1m close) | direction |
|---|---|---|---|
| G1 RELAXED_E1 | E1 anchor survives both validations (+3 bps, 3.6/day) | `trend_15m` aligned with `impulse_dir`; `impulse_atr >= 1.0` (anchor 1.5); `retrace` in [0.20, 0.80] (anchor 0.3-0.7); release = `ret_5 * d > 0` only (the delta / response conditions are DROPPED: V7 showed they add nothing) | trend direction |
| G2 FAILED_BREAK_ELIGIBLE | FAILED_BREAK survives both validations (+2 bps, 25.8/day) but brackets are cost-negative | V7 `failed_break_up/dn` (broke the previous 60-min range within the last 5 bars and closed back inside) AND cost eligibility: the 60-min range width `(range_hi_60 - range_lo_60) / close >= 0.60%` so that the opposite side of the range (the natural target) is at least 4x the round-trip cost | against the break |
| G3 ABSORPTION_AT_LOCATION | ABSORPTION detectable in discovery (+1.6 bps, 26/day) but failed validation 1 on its own | V7 `absorption` flag (strong flow, weak response) AND location: close within 0.5 ATR5m of `range_hi_60`/`range_lo_60`, `swing_high/low_5m` or `prev_day_hi/lo`, with the flow pushing INTO that level | against the flow |
| G4 COMPRESSION_BREAK_FADE | compression breaks REVERSE on average (Δ60 -0.07 to -0.15 ATR) | V7 `COMPRESSION_BREAK` at `compression_pct <= 0.20` | against the break |

Benchmarks carried through every table (the owner's requirement):
- B0 RAW_E1: the V7 E1 anchor exactly as frozen in `config/btc_swing_v7.yaml`, unranked (3.6/day).
- B1 E1_RANKED: the V7 E1 anchor population with the V8 score applied (does ranking add to E1?).
- B2 RELAXED_E1_RANKED: G1 alone with the V8 score (relaxed E1 family + ranking).
- B3 UNION_RANKED: G1 u G2 u G3 u G4 with the V8 score (the V8 system proper).
- B4 UNION_UNRANKED: the whole candidate population traded (one position at a time) — the base rate.

Generator calibration rule (the ONLY permitted adjustment, counts only, no outcome is read): the union
must produce 10-30 candidates per calendar day on TRAIN. If it exceeds 30/day the filters are
tightened in this fixed order until it does not: G2 range width 0.60% -> 0.75%, G3 location 0.5 ->
0.35 ATR5m, G4 compression 0.20 -> 0.15, G1 impulse 1.0 -> 1.25. If it is below 10/day they are
loosened in the reverse order (G1 impulse 1.0 -> 0.75 first, then G4 0.20 -> 0.30, G3 0.5 -> 0.75,
G2 0.60% -> 0.45%). Each step is applied once, the resulting counts are recorded in the report, and
the frozen generator is then fixed. Candidate counts never use labels or forward prices.

## 3. Expected candidate count per day

Estimates from the V7 discovery frequency table (first-in-cluster rates, discovery window):

| generator | V7 reference rate | expected effect of the V8 rule | expected candidates/day |
|---|---|---|---|
| G1 RELAXED_E1 | E1 anchor 3.56/day (impulse >= 1.5, retrace 0.3-0.7, response condition) | impulse 1.0 roughly doubles eligible impulses; dropping the response condition and widening retrace adds ~30-50% | 8-12 |
| G2 FAILED_BREAK_ELIGIBLE | 25.8/day | 60-min ranges >= 0.60% of price are roughly 30-40% of all 60-min ranges in 2022-2024 (ATR5m ~0.1-0.15% -> 60-min range typically 0.4-0.8%) | 7-10 |
| G3 ABSORPTION_AT_LOCATION | 26.2/day | within 0.5 ATR5m of a level and pushing into it: roughly 15-25% of absorption minutes | 4-6 |
| G4 COMPRESSION_BREAK_FADE | 9.4/day | unchanged rule | 8-10 |
| union before de-duplication | | | 27-38 |
| union after 15-min same-direction de-duplication | | overlap between G2/G3/G4 at the same levels is substantial | 18-28 |

Target band 10-30/day; expected 18-28/day; TRAIN has 912 calendar days, so roughly 16k-26k TRAIN
candidates, VALIDATION 1 (184 days) 3-5k and RECENT (181 days) 3-5k. Trading the top 20-30% of 18-28
candidates/day gives 3.6-8.4 nominal entries/day before the one-position constraint; with median holds
of 60-120 min the constraint is expected to bring executed trades into the 3-6/day band at the top-20%
or top-30% cutoff. These are estimates; the measured TRAIN counts are reported in the first table of
the research report and the calibration rule of section 2 applies.

## 4. Feature set `v8-fs-1` (fixed now; all PIT at the candidate minute)

Every feature is a column of the V7 frame or a deterministic function of V7 frame columns at the
candidate minute (no forward data). No feature is added, removed or transformed after the design
freeze. Features are standardised with TRAIN mean/std (stored in the frozen artefact); missing values
(warm-up) make a candidate ineligible rather than imputed.

| group | features (count) |
|---|---|
| candidate identity | generator id one-hot (4); direction; `impulse_atr`, `retrace` (G1; 0 otherwise); range width in bps and in ATR5m (G2); distance to the nearest level in ATR5m (G3); `compression_pct` (G4) |
| structure / location | `dist_struct`, `range_pos_4h`, distance of close to `prev_day_hi` and `prev_day_lo` in ATR5m, distance to `hi_240`/`lo_240` in ATR5m, `impulse_15`, `break_up/dn` within the last 15 min |
| volatility / cost coverage | `atr5m / close` (bps), `atr1m / close`, `rv_15`, `rv_60`, `rv_15 / rv_60`, `vol_expansion`, `range_30 / close`; cost coverage = (0.75% target) / (15 bps) is a constant, so coverage information is carried by `atr5m / close` |
| recent returns | `ret_5`, `ret_15`, 60-min return (ATR5m units), signed in the candidate direction (d * ret) |
| trade intensity | `tc_z`, `burst_5`, `tc_accel` |
| taker flow | `imb_5`, `imb_15`, `delta_accel` (ATR-normalised, signed by d) |
| large-trade activity | `lt_imb_q95_5` (signed by d), `lt_share` |
| delta efficiency / absorption | `impact_5`, `resp_z`, `absorption`, `efficient` flags |
| time of day | hour of day as sin/cos pair, session dummies (Asia 00-07 UTC, Europe 07-13, US 13-21, late 21-24), weekday (Mon-Fri vs weekend) |
| regime | `regime_1h` one-hot, `trend_15m` one-hot, agreement of d with each |
| crowding | candidates emitted in the previous 60 and 240 minutes (any generator) |

About 45 numeric inputs. The flow groups are included although V7 found them weak: the hypothesis is
that their CONDITIONAL value at a located candidate may differ from their unconditional value, and
the ablation of section 6 tests exactly this. Features are signed by the candidate direction wherever
a direction is meaningful, so one model serves longs and shorts.

Stage A (model-free information check, TRAIN only, run before any model is fitted): for every feature
f, the Spearman rank correlation IC(f) between f and the primary label (section 5) over all TRAIN
candidates, its day-cluster bootstrap 95% CI, its sign in each of the five TRAIN half-years, and the
within-day cross-sectional IC (candidates ranked within each day; mean and t-statistic of daily ICs)
versus the pooled time-series IC. Stage A also reports quintile mean net outcomes per feature.
Stage A gate: at least 3 features (outside the candidate-identity group) with |pooled IC| >= 0.03,
CI excluding zero and the same sign in at least 4 of the 5 TRAIN half-years. If the gate fails, no
model is fitted and V8 stops with classification C (section 11).

## 5. Labels (fixed now; computed from forward 1m bars only; never changed after TRAIN)

Entry reference price P0 = open of minute t+1 (taker fill at P0 plus slippage, section 8). All labels
are signed by the candidate direction d.

| label | definition | role |
|---|---|---|
| `ret_30/60/120/180` | d * (close[t+h] - P0) / P0 in bps and in ATR5m units | diagnostic (monotonicity across horizons) |
| `mfe_h`, `mae_h` (h = 30, 60, 120, 180) | max favourable / adverse excursion from P0 using 1m highs/lows, bps | diagnostic; cost coverage = `mfe_180 / 15 bps` |
| `b_050_040` | +0.50% reached before -0.40% within 180 min (first touch on 1m high/low; both in one minute -> stop first, conservative) | secondary bracket |
| `b_075_050` | +0.75% before -0.50% within 180 min | PRIMARY bracket |
| `b_100_060` | +1.00% before -0.60% within 180 min | secondary bracket |
| `net_075_050` | realised net P&L in bps of the primary bracket traded as an order: taker entry at P0, limit target +0.75%, stop -0.50% with stop slippage, time stop at the 180-min close (taker), minus fees, spread, slippage and funding accrued (section 8) | PRIMARY LABEL (regression target; classification target = 1{net > 0}) |
| `net_050_040`, `net_100_060` | the same for the secondary brackets | secondary |
| `r_075_050` | `net_075_050` / 50 bps (R multiple at the 0.50% stop) | reporting in R |

Mirrored shorts are the same definitions with d = -1 (target below, stop above). Unresolved brackets
exit at the 180-min close; they are included (never dropped) so that every candidate has a realised
net outcome. The three brackets are reported for every table; the primary bracket is the one the
score is trained on and the one the success criteria use. Rationale for the primary choice (fixed
before any V8 data is read): V7 shows the +-1% brackets are the only ones net positive for E1, but a
1% target against a 0.6% stop needs median holds beyond 3 h for a 3-6/day system; +0.75%/-0.50% is
the pre-registered compromise (1.5 reward/risk, breakeven resolved win rate 40% gross, 52% net at
15 bps). Label definitions are never optimised on any window.

## 6. Models and scores to compare (all pre-declared; fixed hyperparameters; no grids)

| id | score | specification |
|---|---|---|
| S0 | none | unranked candidate population (benchmark B4) |
| S1 | MONOTONE_SIMPLE | sum over the Stage A gated features of sign(IC) x z-scored feature (equal weights); signs and the feature list taken from TRAIN Stage A only; deterministic, no fit beyond means and stds |
| S2 | LOGISTIC | L2 logistic regression on all `v8-fs-1` features (standardised), C = 1.0, target 1{`net_075_050` > 0}; scikit-learn (already a project dependency); deterministic solver |
| S3 | SHALLOW_BOOSTED | scikit-learn HistGradientBoostingClassifier, max_depth 2, 150 iterations, learning rate 0.05, min_samples_leaf 200, l2 1.0, no early stopping on validation, fixed seed 17; monotonic constraints applied to every feature whose Stage A sign is stable (4 of 5 half-years), unconstrained otherwise; same target as S2 |
| S4 | COST_COVERAGE_ONLY (control) | score = `atr5m / close` only (higher expected move relative to the fixed cost); exists to test whether any apparent ranking edge is merely volatility selection |
| S5 | TIME_ONLY (control) | score = TRAIN mean label by (hour, weekday class) cell; tests whether the edge is a time-of-day effect |

No neural network, no deep or wide ensembles, no stacking, no hyperparameter search of any kind. A
model is a candidate for the frozen artefact only if it passes the Stage A gate (S1, S2, S3) and the
TRAIN cross-validation criteria of section 7.

Pre-declared ablations (TRAIN cross-validation only, informational): S2 on structure + volatility +
time features only; S2 on flow features only (intensity, taker flow, large trades, efficiency); S2 on
everything. This answers whether the flow groups add anything in the conditional setting.

## 7. Train / validation protocol

| window | span | days | use |
|---|---|---|---|
| TRAIN | 2022-01-01 -> 2024-06-30 | 912 | generator calibration (counts only), Stage A, model fitting, model selection, cutoff selection, freeze |
| VALIDATION 1 | 2024-07-01 -> 2024-12-31 | 184 | frozen artefact, evaluated once |
| RECENT | 2025-01-01 -> 2025-06-30 | 181 | frozen artefact, evaluated once |
| LOCKED HOLDOUT | 2025-07-01 -> 2026-09-30 | | not downloaded, not read |

Order of work, each step gated by the previous:
1. Build `btc_swing/v8/` and `config/btc_swing_v8.yaml`; tests; commit; record the design commit hash
   and the config hash in the report manifest.
2. Candidate generation on TRAIN; calibration rule of section 2 (counts only); freeze the generator.
3. Labels on TRAIN; Stage A; Stage A gate. On failure: report, classification C, stop.
4. TRAIN cross-validation for S1, S2, S3, S4, S5: five chronological folds by TRAIN half-year,
   expanding window (fit on the half-years before fold k, score fold k; fold 1 = 2022H1 is scored by
   a model fitted on 2022H1 itself and reported separately as in-sample), with a 1-day purge/embargo
   at every fold boundary so no label window (180 min) crosses into the fit data. Out-of-fold scores
   from 2022H2 onward form the TRAIN selection table.
5. Model selection, written rule: among S1, S2, S3 take the SIMPLEST model (order S1 < S2 < S3) whose
   out-of-fold top-30% net expectancy is within 1.0 bps of the best of the three; it must also beat S4
   and S5 by >= 2 bps at top-30% and show monotone buckets (Spearman of bucket index vs bucket mean
   >= 0.8) out of fold; otherwise selection fails and section 11 applies.
6. Cutoff selection on TRAIN: score cutoffs for the top 5/10/20/30% of TRAIN out-of-fold scores are
   converted to ABSOLUTE score values. The trading cutoff is the one among top-10/20/30% whose
   one-position executed simulation on TRAIN produces 3-6 trades per calendar day; if several, the
   strictest; if none, the one closest to 4.5/day is reported and criterion F1 of section 10 is
   expected to fail.
7. Refit the selected model on all of TRAIN; serialise generator parameters, feature means/stds,
   Stage A signs, model parameters, absolute cutoffs and the dataset hash into
   `reports/v8/frozen_artefact.json` with a sha256; commit. Nothing after this commit changes before
   the validation tables are produced.
8. Evaluate the frozen artefact once on VALIDATION 1 and once on RECENT (absolute cutoffs; per-window
   quantile cutoffs reported alongside as a diagnostic only). Write the report. Stop.

Secondary diagnostic (not used for the verdict, pre-declared so it is not a post-hoc rescue): a
walk-forward variant that refits the selected model at the end of TRAIN (for VALIDATION 1) and at
the end of VALIDATION 1 (for RECENT) is reported next to the frozen artefact, to show whether drift
rather than absence of information explains any validation shortfall.

Executed simulation (same for every table that reports trades/day): candidates sorted by time; a
candidate is executed only if its score is >= the cutoff and no position is open; the bracket of the
primary label is the trade (entry open of t+1, target +0.75%, stop -0.50%, time stop 180 min);
position size by the frozen `btc_swing/risk/sizing.py::size_position` with `risk_per_trade` 0.15%,
leverage ladder, margin cap; equity and drawdown tracked; funding at the 8-hour marks while open.

## 8. Cost assumptions (Bybit linear perpetual, taker-first; identical to V6.1 primary)

| item | value | note |
|---|---|---|
| taker fee | 5.5 bps per side | entry, stop and time-stop exits are taker |
| maker fee | 2.0 bps | target exits only (limit at the target); maker-first ENTRY is reported as a sensitivity only, never as the primary |
| entry slippage | 1.0 bps | added to the taker entry |
| stop slippage | 3.0 bps | added to stop exits |
| spread | 1 tick (0.05 USDT) half-spread each side | negligible at BTC prices; included |
| funding | Binance USDT-M funding series as the proxy (Bybit history not reachable), accrued at the 8-hour marks while a position is open | informational for 30-180 min holds |
| round trip, primary bracket | ~15 bps on notional if stopped (5.5 + 1 + 5.5 + 3), ~13 bps if the target fills as maker, ~12 bps on a time stop | = 0.30 R at the 0.50% stop; this is the number the ranking must beat |
| sensitivity | all tables repeated at 1.5x costs (~22 bps) | PASS requires base costs; "robust" additionally requires net > 0 at 1.5x |

Costs are applied per candidate in the labels (section 5) and per trade in the executed simulation.
No cost is assumed below Bybit's published VIP0 non-member schedule.

## 9. Anti-overfitting controls

1. This design is frozen by commit hash before any V8 code reads data; the report cites that hash and
   the V7 dataset hash; the config schema is strict (unknown keys rejected).
2. Fixed feature list `v8-fs-1`, fixed label definitions, fixed hyperparameters, fixed model list; no
   grid, no feature selection by validation, no label search. The only adjustment permitted is the
   generator count calibration of section 2, which reads candidate counts and never outcomes.
3. Stage A gate: no model is fitted unless model-free rank information is demonstrated on TRAIN.
4. Chronological purged cross-validation with a 1-day embargo; out-of-fold scores only for selection.
5. Written selection rule favouring the simplest model; selection, cutoffs and the artefact are fixed
   on TRAIN and serialised with a sha256 before validation data are touched.
6. Validation windows are evaluated once each with absolute cutoffs; there is no second pass.
7. Statistics: day-cluster bootstrap (1000 resamples, seed 17) for every expectancy and difference;
   nothing is reported as significant on observation-level resampling.
8. Label-permutation null: 200 permutations of the primary label among the candidates of the same day
   (preserving day structure and the number of candidates per day); the top-30% net expectancy of the
   frozen score must exceed the 95th percentile of the null distribution on the pooled validation year.
9. Control scores S4 (volatility only) and S5 (time only) must be beaten at equal trade counts; a
   ranking that only reproduces them is reported as such (classification B-vol / B-time, section 10),
   never as an edge.
10. Benchmarks B0-B4 in every table so that "ranking added X bps" is always read against raw E1 and
    the unranked population.
11. Multiplicity declared now: the single confirmatory test is {selected model, primary label,
    primary bracket, TRAIN-selected cutoff, pooled validation year}; everything else (horizons,
    secondary brackets, other buckets, ablations, walk-forward variant, cost sensitivity) is
    diagnostic and cannot upgrade the classification.
12. Holdout protection: the loader refuses any day >= 2025-07-01 (as V7); the ingestor is not run.
13. Separation: `btc_swing/v8/` imports V7 feature code read-only; no V5/V5.1/V6/V6.1/V7 file is
    modified.

## 10. Success criteria (precise; evaluated on the frozen artefact at the TRAIN-selected cutoff)

Frequency (F), on VALIDATION 1 and RECENT separately unless stated:
- F1 mean executed trades per calendar day in [3.0, 6.0] in each window;
- F2 median executed trades per day >= 2 in each window; share of days with 0 trades <= 25%;
  distribution reported as % days with 0, 1-2, 3-6, > 6 trades;
- F3 median hold between 30 and 180 minutes (by construction; reported).

Edge (E), base costs, primary label:
- E1 net expectancy per executed trade > 0 in each window, and on the pooled validation year the
  day-cluster bootstrap 95% CI lower bound > 0;
- E2 profit factor (gross wins / gross losses, net of costs) > 1.10 pooled, > 1.0 in each window;
- E3 maximum equity drawdown < 10% in each window at 0.15% risk per trade (the V6 hard limit);
- E4 top-30% net expectancy exceeds the unranked population (B4) by >= 3 bps per trade and exceeds raw
  E1 (B0) net expectancy on the same window.

Ranking validity (R):
- R1 monotonicity: bucket mean net expectancy over {top 5, top 10, top 20, top 30, rest} is
  non-increasing with Spearman(bucket index, bucket mean) >= 0.8 on the pooled validation year and
  the top-30% bucket is strictly above the rest; a non-monotone score is REJECTED whatever its top
  bucket shows;
- R2 beats S4 (volatility only) and S5 (time only) by >= 2 bps net at the same trade count;
- R3 top-30% net expectancy above the 95th percentile of the label-permutation null.

Classification (written now):
- A (PASS): all F1-F3, E1-E4, R1-R3 hold. Next step would be a NEW owner pre-registration for a
  forward paper observation; the locked holdout is still not spent by this track.
- B (PARTIAL): E1 holds pooled and R1 holds but F1 fails with 2.0-3.0 or 6.0-8.0 trades/day, or E1
  holds in one window only, or R2 fails against exactly one control (reported as B-vol or B-time,
  meaning the information is volatility or time-of-day, not candidate context). Owner decision; no
  further search inside V8.
- C (FAIL): anything else, including every outcome in section 11. V8 is closed.

Hard reporting rules (owner's instructions, binding): a result with 0.5-1 executed trades/day is
never reported as success even if net positive; trades are never manufactured to reach 3/day (the
cutoff is selected on TRAIN by the rule of section 7 and not moved); the quality cutoff is never
lowered to meet frequency.

## 11. Evidence that would make us abandon V8

Any one of the following closes V8 as classification C, with the report written and no further variant tried:

1. Stage A fails on TRAIN: fewer than 3 non-identity features with |IC| >= 0.03, CI excluding zero and
   a stable sign in >= 4 of 5 half-years. Interpretation: candidate context carries no rank information;
   no model is fitted.
2. Gross spread too small to pay costs: on TRAIN out-of-fold, the gross (pre-cost) expectancy gap
   between the top-30% and the bottom-30% of the best score is < 10 bps per trade, or the gross
   expectancy of the whole candidate population is < 0 and the top-30% gross is < 15 bps. No ranking
   of this population can clear a 15 bps round trip.
3. TRAIN selection fails: no model among S1-S3 beats S4 and S5 by >= 2 bps at top-30% out of fold, or
   none has monotone buckets. The information, if any, is volatility or time of day.
4. Validation net expectancy <= 0 in either window at the TRAIN-selected cutoff.
5. Monotonicity fails on the pooled validation year (R1), whatever the top bucket shows.
6. The label-permutation null is not exceeded (R3): the top slice is consistent with chance.
7. Only a low-frequency slice is net positive: the smallest cutoff with net > 0 on validation yields
   < 2 executed trades per day. V8 would then be another 0.5-1 trade/day system and is abandoned as
   an intraday objective, with the low-frequency slice recorded for the owner but not recommended.
8. The frozen artefact's validation result is positive only under the walk-forward refit variant:
   drift sensitivity of this size is treated as failure of the frozen hypothesis, not as a success.
9. Infrastructure evidence: a PIT violation is found in any V8 feature or label (any use of a row with
   close_time > t, or a threshold derived from the same day), or the dataset integrity check fails.
   The run is invalid until fixed and re-run from step 1 with the fix recorded; a second such finding
   after the fix closes the track.

What V8 will NOT do under any outcome: touch the locked holdout, re-ingest or re-download trades,
activate anything, change V5.1 or any closed generation, place a Bybit order, tune thresholds, labels
or hyperparameters after the freeze, or introduce a neural network.

## 12. Deliverables if approved

- Code: `btc_swing/v8/{config,candidates,labels,features,rank,evaluation,research,report,cli}.py`,
  `config/btc_swing_v8.yaml`, `tests/v8/`, CLI `btc-swing v8 config|candidates|stage-a|train|freeze|validate|render`.
- Report `reports/BTC_SWING_V8_CANDIDATE_RANKING_RESEARCH.md` with, in order: manifest (design and
  config hashes, dataset hash, windows, holdout "not read"); candidate counts per generator and the
  calibration steps; Stage A table; TRAIN cross-validation tables (per score: gross / fees / slippage /
  funding / net / PF / trades per rank bucket top 5/10/20/30%/all; monotonicity; ablations); the
  frozen artefact hash; validation tables per window and pooled (same columns; benchmarks B0-B4;
  controls S4/S5; permutation null; cost sensitivity 1.5x); frequency tables per cutoff (trades/day,
  median/day, % days 0 / 1-2 / 3-6 / > 6, median hold); the classification against section 10 and 11.
- Tables `reports/v8/` (JSON), reproducible with `btc-swing v8 …` on the existing dataset.
- Expected compute: candidate generation and labels over 1.9 M minutes are the same order as the V7
  discovery run (minutes); model fits on ~20k rows are seconds; no ingest.

Design author's expectation, recorded for honesty: given V7 (flow < 1 bp) and V2 (ranking of swing
candidates: no edge), the most likely outcome is that Stage A passes on volatility, time-of-day and
location features, that S1/S2 produce a monotone but shallow spread (5-10 bps gross between top and
bottom), and that the top-30% slice is around break-even after costs on validation (classification B
or C). The design is worth running because it is cheap, because it is the first track to ask the
conditional question on a population large enough to answer it, and because a negative answer here
would establish that intraday candidate selection on this data cannot pay Bybit taker costs, closing
the 3-6 trades/day objective on evidence rather than on another fixed-rule failure.
