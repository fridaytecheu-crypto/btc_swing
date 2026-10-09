# BTC_V5_1_DATA_QUALITY_FIX — V5.1 (prospective; frozen V5 rules on valid observations only)

Owner request 2026-10-09 after the authoritative Mac diagnostic showed two structural data-quality
blockers in the frozen V5 forward observation: `oi_chg_1h_z` only 550/2880 observations warm
(open interest exists only from live collection) and `vol_1h_z` corrupted by gap-filled outage
rows (595 rows below 1 BTC/h, log-volume down to about -27.7, baseline std inflated about 10.6x,
z >= 1 needing about 1.1 million BTC in one hour). V5.1 is a DATA-QUALITY fix, not a strategy
change: no threshold, event definition, feature definition, risk, stop, target, leverage or exit
rule changed; nothing was optimised against outcomes; no historical forward signal or trade was
created; no order was placed while implementing or validating it.

## 1. What is preserved (V5)

- `config/btc_swing_v5.yaml` (hash `d18ebf19bd0c…`), `manifests/v5_forward_freeze.json`, the V5
  observation start `2026-10-07T14:59:22.972Z`, the V5 signal/outcome journals, paper ledger and
  Demo journals are untouched; `btc_swing/v5/` is not modified except for parametrisation of the
  demo plumbing (orderLinkId prefix, freeze check, activation version) whose defaults keep V5
  behaviour byte-identical (the V5 tests cover it).
- V5 keeps running in the same runner on the same collector and bars and stays reproducible.

## 2. Root causes and the fix (`btc_swing/v51/`)

| root cause (frozen V5) | V5.1 (`v5.1-dq-1`) |
|---|---|
| Collector outages are carried forward as zero-trade rows (NaN prices forward-filled, volume 0, ticker fields such as OI stale) and the seed->forward gap is filled the same way; the frozen rolling z-scores treated them as market observations. The sliding 1h volume sum of outage rows left a float residue (~1e-12 BTC) whose log (~ -27) entered the 30-day baseline. | A 5m row is a valid observation iff `gap_filled == 0` and `trades > 0`. Every rolling feature has its frozen lookback (`features.LOOKBACK`); the observation at row k is valid only if row k and its whole lookback are valid. Invalid observations are NaN: not a current value, and excluded from every rolling mean/std (the frozen `rolling_z` ignores NaN). Rows are retained for coverage/audit; nothing is interpolated or backfilled. |
| Open interest has no archive history: the 2880-observation warm-up needs ~10 days of live data. | Bybit PUBLIC `/v5/market/open-interest` (5min) history seeds the OI series for the 30 days before the V5 observation start (warm-up only, section 3). |
| Wilder ATR(1h) smoothed zero-range outage hours. | ATR(1h) recomputed with the frozen Wilder recursion over clean 1h bars only (a 1h bar is clean if its 12 rows and the previous bar's 12 rows are valid); contaminated bars have no ATR. Event geometry additionally needs a clean 12-bar structural window; otherwise `atr` is NaN and every frozen detector is off at that row. |
| Premium / funding warm up forward only. | Optional seeds from mark/index 5m klines (`mark_close/index_close - 1`, the live row's own definition) and `/v5/market/funding/history` (settled rate at settlement time, the live event definition); verified against live rows on the overlap. They are the crowding-filter inputs only (NaN passes), so correctness mattered more than warm status. |

Config: `config/btc_swing_v5_1.yaml` = the V5 file with `strategy_name` changed and ONLY the
`data_quality` block added (tests prove every strategy section identical). V5.1 config hash
`144f7d58bb7150c7f9084fcbd2b5547537ec677c5f92fa4628fd025c502d60d7` (pinned in
`btc_swing/v51/config.py`). Rule versions: features `v5.1-feat-1` (= `v5-feat-1` on clean data),
events `v5-event-1`, exits `v5-exit-1`, data quality `v5.1-dq-1`.

## 3. Historical seeds (PIT rules)

`btc-swing v51 seed oi|premium|funding` (on the Mac; Bybit REST is geo-blocked from the cloud
container): every page is persisted verbatim and immutably (`<data>/btc/forward_v51/seeds/<kind>/raw/`,
sha256, request params, `retrieved_at`), every table row carries `source`, `retrieved_at`,
`page_sha256`. `split_warmup` keeps rows strictly BEFORE the V5 observation start as feature
warm-up; rows at/after it are used only to verify timestamp semantics, units and alignment
against the live collector rows (`verification.json`): OI `timestamp` = the 5m boundary = our bar
`close_time` (control: the bar opening at it), BTC on both sides (unit ratio ~1); kline closes =
the last value of the bar = the live `mark_close`/`index_close`; funding settlement time = the
live funding event. Seeds never create a signal or trade, never fill a collector outage after the
V5 start, and never change the V5 journals.

## 4. Equivalence proof (`tests/v51/`, 28 tests)

1. Continuous clean data: every V5.1 feature column, regime, Stage A mask and strength equals
   frozen V5 exactly (tolerance 1e-12) at every row whose z window lies inside the frame; the only
   differences before that are frame-start rows V5 defines before their lookback exists (which the
   45-day seed keeps out of every evaluated row on the real stream).
2. Differences occur only where V5 consumed gap-contaminated data (zero-volume synthetic rows,
   residue log-volume, 1h windows touching gaps) or lacked warm-up history (OI seed).
3. No threshold changed; 4. no family definition changed; 5. no risk/sizing/TP/SL/leverage rule
   changed (`test_v51_config.py`: strategy sections byte-identical to V5); 6. no historical
   forward signal or trade (`test_v51_forward.py`: V5.1 signals only after its own start; V5 state
   fingerprint unchanged by a V5.1 cycle).
Regression tests also cover: rolling z ignoring invalid observations, clean-bar ATR (poisoned
contaminated true ranges change nothing), OI seed alignment/units/PIT split, no future leakage,
restart/recovery (V5.1 executor adopts only `V51D-` orders), the read-only diagnostic.

## 5. Running V5.1 on the Mac (do NOT activate until every gate passes)

```bash
cd ~/btc_swing && git pull origin main && uv sync --all-extras
export BTC_DATA_DIR="$HOME/btc_swing_data"
uv run pytest -q                                              # full suite
uv run btc-swing v51 seed oi                                  # 30 d of 5-min OI before the V5 start + alignment check
uv run btc-swing v51 seed premium                             # optional (mark/index klines), verified
uv run btc-swing v51 seed funding                             # optional (funding history), verified
uv run btc-swing v51 seed status
uv run btc-swing v51 forward signal-diagnostic                # FEATURE | VALID OBS | WARM | CURRENT | Z | QUALITY + families
uv run btc-swing v51 forward compare-diagnostic               # frozen V5 vs V5.1, cause of every difference
uv run btc-swing v51 forward freeze                           # manifests/v5_1_forward_freeze.json (V5.1 observation start = now)
bash deploy/macos/stop_macos.sh && bash deploy/macos/start_macos.sh "restart with V5.1 pipeline"   # runner picks up V5.1 after its freeze
uv run btc-swing v51 forward host-status
```
Activation (owner only, later): `btc-swing v5 demo deactivate --note "..."` (V5 must be inactive
and flat; one BTC position per account), then `btc-swing v51 demo activate --note "..."`, which
runs the test suite, both preflights, the PASSED smoke, the V5.1 freeze, integrity, reconciliation,
sizing AND the V5.1 gates (all detector inputs warm on valid observations, `oi_chg_1h_z` >= 2880
valid observations, `vol_1h_z` reachable, gap-free current window, OI seed verified, V5 demo
inactive and flat, no safety blocker). Only then is `STRATEGY_DEMO_ACTIVATED` (version V5.1)
appended to `<data>/btc/forward_v51/demo/strategy_journal.jsonl`; V5.1 orders use `V51D-` ids.
