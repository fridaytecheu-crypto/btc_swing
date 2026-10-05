# BTC Swing V2 — Cost-Aware Learned Opportunity Ranking (design)

Status: research design, written before any V2 model was fitted. Paper/backtest only. No live
trading, no exchange keys, no real money. V1 artefacts (code paths outside `btc_swing/v2`,
`config/btc_swing.default.yaml`, `reports/BTC_SWING_V1_*`, `manifests/*`) are immutable and are
not modified by V2.

## 1. Why V2 exists (V1 closing summary)

V1 is closed as FAILED OUT-OF-SAMPLE AS A TRADING STRATEGY, SUCCESSFUL AS RESEARCH
INFRASTRUCTURE. What V1 established, with frozen pre-registered defaults and a single untouched
confirmatory run (`reports/BTC_SWING_V1_PHASE3_UNTOUCHED_VALIDATION.md`):

- setup discovery finds directional movement: gross expectancy was positive in every window
  (+0.05R to +0.11R per trade on 2025-2026, +0.14R to +0.21R on 2022-2024);
- manual entry-mechanics changes (Phase 2.1, 2.2) and the exit change (Phase 2.3) did not help;
- blocking new SHORT entries in TREND_DOWN (Phase 2.4) helped consistently, in sample and on the
  2025-2026 holdout (+0.04R, drawdown 11.3% -> 7.0%), but not enough;
- the 2025-2026 holdout was net negative for both arms (-0.10R / -0.07R);
- realistic fees, slippage and funding (0.16R to 0.18R per trade) exceed the gross edge.

So the binding constraint is opportunity selection and edge strength per trade, not leverage or
exit mechanics. V2 asks whether a learned, cost-aware ranking can pick a substantially smaller
subset of the same opportunities whose realised net expectancy survives costs.

**Central question.** Can a PIT-safe, cost-aware learned ranking identify a substantially smaller
subset of BTC swing opportunities whose realised net expectancy survives realistic fees, slippage
and funding across different market regimes?

## 2. Validation constraint (stated up front)

2025-01 .. 2026-09 was inspected in V1 Phase 3. It is NOT an untouched holdout any more. V2 uses
the whole 2022-01 .. 2026-09 history chronologically (walk-forward), and no V2 result on this data
may be described as untouched or out-of-sample confirmation. The next genuine validation of V2 is
future forward paper testing after V2 is frozen. The V2 report says this in its first section.

## 3. What V2 reuses from V1 (unchanged)

Immutable Binance Vision archive and dataset store; `MultiTfSeries` / `MarketView` (PIT bars and
causal indicators on 5m/15m/1h/4h/1d); the regime classifier; the eight setup detectors and the
episode state machine; `AuxSeries` (funding, open interest, long/short and taker ratios, premium
index, mark price, volume acceleration); risk sizing and the isolated-margin liquidation model;
the cost model (taker 5 bps, slippage 2/5 bps, archive funding); the bar-path exit engine (stop
first, TP1/TP2, breakeven after TP1, structural trail, 240 h cap, mark-price liquidation); the
geometry-matched null benchmark; deterministic manifests and hashing; report tooling. V2 imports
these; it does not copy or rewrite them. The V1 configuration file is loaded as the frozen
execution framework (config hash recorded in every V2 manifest).

## 4. V1 setups become candidate generators

A detected setup is a **candidate opportunity**, not a trade. V2 runs the V1 episode lifecycle
(WATCH -> zone reached and confirmed -> ENTRY_READY -> 5m trigger -> TRIGGERED) with two
differences that only matter for *which opportunities are recorded*, not for how any opportunity
is defined:

1. one episode lifecycle per family runs independently (eight `EpisodeManager` instances with one
   detector each), so a WATCH in one family does not censor another family's candidates;
2. nothing occupies a slot: a TRIGGERED episode is recorded as a candidate and the family then
   observes the V1 post-close cooldown (12 bars) and anchor de-duplication, as after a V1 trade.

The candidate decision time is the close of the 5m bar whose trigger fired; the hypothetical fill
is the next 5m open plus entry slippage, exactly as in V1. Candidates that the frozen risk rule
rejects (`RISK_REJECTED`) are counted but carry no label and are excluded from modelling. The V1
single-slot traded population (a V1 CONTROL run over the same history) is kept as a baseline.

## 5. Feature set `v2-fs-1` (fixed, PIT-safe, documented in code)

All features are computed at the candidate decision time t from the `MarketView` at t (completed
bars only), the plan frozen at detection, the regime journal up to t, and the `AuxSeries`
snapshot at t (observation time + latency <= t). Nothing after t is read; the truncation audit
in the report re-runs candidate generation and feature computation with later data removed.

Setup: family (one-hot), side (+1 LONG / -1 SHORT), setup age (5m bars since detection), bars
since confirmation, distance of the close from the zone mid and from the structural anchor (in
setup-TF ATR, signed in the trade direction), stop distance (ATR and %), structural target in R
(NaN when the plan has none), plan zone width in ATR.
Price / momentum (signed in the trade direction unless noted): returns over 15m, 1h, 4h, 24h, 7d;
distance of the close from EMA20/EMA50 on 1h and 4h and from EMA200 on 1d (ATR units); EMA20-EMA50
spread on 1h/4h/1d; EMA50 slope on 1d; position in the 20-bar range on 4h and 1d (0..1, unsigned,
plus the trade-direction version); retrace from the last 1h swing extreme (ATR); volume
acceleration 5m and 1h; ATR% on 1h/4h/1d; ATR expansion (ATR / ATR ten bars earlier) on 1h and 1d;
1d ATR% percentile over the previous 90 completed 1d bars; realised volatility of 5m returns over
24 h and 7 d.
Regime: regime (one-hot), regime age in 5m bars, number of regime transitions in the previous
7 days, `short_in_trend_down` (the V1 structural finding as a feature).
Derivatives: last funding rate, mean of the last three, funding minus that mean, funding z-score
against the previous 90 funding observations; open interest (log), OI change 1h/4h/24h, price
change 24h, price-change x OI-change interaction (divergence), long/short ratio of accounts, top
trader long/short positions, taker long/short volume ratio, taker buy ratio 1h and 4h and their
difference, premium index, premium 1h mean, premium minus its 1h mean, last minus mark (%).
Context: hour of day and day of week (sine/cosine), weekend flag, distance from the 30-day high
and from the 30-day low (%), realised volatility 24h (also listed under momentum).

Missing values (early history of a dataset, the two missing premium/mark days) are imputed with
the training-fold median; a per-row `n_missing` count is a feature.

## 6. Labels (frozen V1 execution engine)

For every risk-accepted candidate the V1 position is simulated from the next 5m open with the
frozen sizing (0.5% of a fixed 10,000 USDT research equity, V1 leverage ladder, liquidation
constraints), the frozen stop/TP1/TP2/breakeven/trail/time-cap rules, mark-price liquidation and
the frozen fee, slippage and funding model. Each candidate is simulated independently of every
other one (no slot). Labels: realised net R, gross R, fees, slippage, funding, MFE/MAE (R),
holding hours, exit reason, TP1 hit, counterfactual 1.5R-before-initial-stop, and
`stop_before_target`. A candidate whose simulation ends with END_OF_DATA has no label.

Modelling targets: A. `y_pos = 1[net R > 0]`; B. `net R` clipped to [-2, 4] (robust expected net R).

## 7. Models (pre-declared, no search)

- M1 logistic regression on standardised features for P(net R > 0); L2, C = 1.0.
- M2 ridge regression on clipped net R; alpha = 10.
- M3 `HistGradientBoostingClassifier` (max_depth 3, learning_rate 0.05, max_iter 200,
  min_samples_leaf 50, l2 1.0; NaNs native) — fitted ONLY if the M1 gate below passes.
- Ablation: M1 without the derivatives block (one ablation, pre-declared).
- Model + hard block: M1 ranking applied to candidates not blocked by NO_NEW_SHORT_IN_TREND_DOWN.

`candidate_score` = M1 probability; `P(net_R > 0)` = M1; `expected_net_R` = M2. No other model,
no hyper-parameter search, no deep learning, no LLM.

M3 gate (pre-declared): M1 walk-forward Spearman(score, net R) > 0 overall AND the M1 PIT
top-quartile slice beats the all-candidates net expectancy by more than +0.05R.

## 8. Chronological walk-forward

Quarterly test blocks from 2023-Q1 to 2026-Q3 (15 folds). For a block starting at S, the training
set is every labelled candidate with decision time >= 2022-01-01, decision time < S and simulated
exit time <= S (its label is known at S). Expanding window; models are refitted per fold on the
training set only (imputer and scaler included). Every candidate from 2023-01-01 on receives
exactly one walk-forward prediction from a model that saw only strictly earlier, fully resolved
candidates. 2022 candidates are training-only.

Selectivity thresholds are PIT: the "top q%" threshold used in a block is the (1 - q) quantile of
the model's scores on its own training set, so no test-block information enters the selection.

## 9. Evaluation

- Ranking: Spearman(score, net R) overall and per fold; decile means of net R (OOF deciles);
  monotonicity = Spearman between decile index and decile mean; AUC of M1; top-N per month.
- Selectivity vs frequency: slices all / top 50% / 25% / 10% / 5% (PIT thresholds), each with
  candidates/month, trades/month after single-slot sequencing, net expectancy, gross expectancy,
  PF, win rate, total fees/slippage/funding, max drawdown, account return. A sequential single-slot
  account (fixed research equity sizing as in V1) converts a slice into a tradeable stream.
- Baselines: all candidates; V1 single-slot traded population; time-matched and regime-matched
  geometry null (K = 5 per candidate); simple rule baseline (NO_NEW_SHORT_IN_TREND_DOWN hard
  block, unranked); model + hard block.
- Breakdowns: LONG/SHORT, family, regime at decision, derivatives-feature diagnostics (univariate
  Spearman per derivatives feature; M1 coefficients; the ablation), costs, drawdown, stability
  (per-fold AUC/Spearman/top-quartile expectancy, coefficient sign consistency across folds).

## 10. Risk architecture (frozen from V1)

0.5% planned account risk per trade; same leverage ladder (1/2/3/5/10x under the 25% margin cap);
same liquidation constraints (stop-to-liquidation >= 3x, >= 3 ATR); isolated margin; same fee,
slippage and funding model. V2 does not touch leverage or risk.

## 11. Pre-declared classification (encoded in `btc_swing/v2/evaluation.py`)

Criteria evaluated on the walk-forward predictions (2023-01 .. 2026-09), M1 ranking:
1. Spearman(score, net R) > 0 overall, and > 0 in at least 70% of folds with >= 20 candidates;
2. decile monotonicity: Spearman(decile index, decile mean net R) >= 0.6;
3. PIT top-25% slice: net expectancy > 0 and PF > 1.0 overall, and net expectancy > 0 in both
   halves (2023-2024 and 2025-2026);
4. the top-25% slice beats the all-candidates net expectancy by >= +0.10R and beats both the
   time-matched and regime-matched null means;
5. the top-25% slice, after single-slot sequencing, yields >= 2 trades per month on average;
6. stability: the top-25% slice has positive net expectancy in >= 60% of folds with >= 5 selected
   candidates.
A — RANKING EDGE DEMONSTRATED, FREEZE FOR FORWARD PAPER TEST: all six met.
B — SOME SIGNAL, MORE CONTROLLED RESEARCH REQUIRED: criterion 1 met and the top-25% expectancy
exceeds the all-candidates expectancy, but any other criterion fails.
C — NO USEFUL RANKING EDGE: otherwise.
Thresholds are not moved after seeing results. A "B" does not license a filter search.

## 12. What V2 will not do

No grid search of indicator periods; no TP/SL or leverage optimisation; no repeated removal of
losing families or regimes; no re-running until a profitable configuration appears; no treating
2025-2026 as untouched; no live or paper trading engine in this deliverable.

## 13. Deliverables and layout

`btc_swing/v2/` — `config.py` (V2 config schema + hash), `candidates.py` (candidate generator),
`labels.py` (frozen-execution labels), `features.py` (`v2-fs-1`), `walkforward.py` (folds and
models), `evaluation.py` (buckets, slices, sequential account, baselines, criteria), `report.py`.
`config/btc_swing_v2.default.yaml`; CLI `btc-swing v2 research`; tests `tests/test_v2_*.py`;
`reports/BTC_SWING_V2_RANKING_RESEARCH.md` (21 sections + one classification); run manifest in
`manifests/`.
