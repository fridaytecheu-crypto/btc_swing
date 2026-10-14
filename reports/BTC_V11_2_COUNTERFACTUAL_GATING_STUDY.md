# BTC V11.2 — counterfactual family-gating study (RESEARCH ONLY)

Generated 2026-10-14T10:20:27.639936+00:00 · V11 strategy `a324dbcb0394` · code `021679d6e27d-dirty` · Bybit 1m dataset `92d27161c194` · TRAIN 2022-01-01 -> 2024-07-01, VALIDATION 2024-07-01 -> 2025-07-01 · locked holdout: not read (every load capped at 2025-07-01).

Each variant changes ONE family gate of the frozen V11 engine; features, score model, thresholds, risk, exits and primary costs are identical. V11_BASE is the current frozen V11 engine (commit 98445d6+): it matches the committed V11 backtest trade for trade on entries and gross R, while its net is ~0.008R lower because that report ran before the post-TP1 stop-leg label fix (those exits were charged 1 bp exit slippage instead of the 3 bp stop slippage). **Selection bias:** this is a multi-variant search on consumed data, so the best variant's numbers are optimistic; nothing here is frozen, and no shadow runner exists for V11.2.

## TRAIN

**A / B / C independent variants**

| variant | trades/day | trades | gross R | cost R | net R | PF | win | max DD % | med hold | A/B/C | L/S | Δ trades/day | Δ gross R | Δ net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V11_BASE | 2.68 | 2445 | -0.016 | 0.374 | -0.389 | 0.21 | 0.402 | 142.8 | 20 | 1405/995/45 | 1247/1198 | +0.00 | +0.000 | +0.000 |
| A1_NEUTRAL_OK | 3.86 | 3521 | -0.003 | 0.358 | -0.361 | 0.22 | 0.391 | 190.8 | 20 | 2615/864/42 | 1807/1714 | +1.18 | +0.012 | +0.028 |
| A2_WITHIN2 | 2.72 | 2481 | -0.015 | 0.373 | -0.388 | 0.21 | 0.402 | 144.2 | 20 | 1443/993/45 | 1264/1217 | +0.04 | +0.001 | +0.002 |
| A3_WITHIN3 | 2.78 | 2535 | -0.014 | 0.372 | -0.386 | 0.21 | 0.403 | 146.7 | 20 | 1509/981/45 | 1289/1246 | +0.10 | +0.002 | +0.004 |
| B1_NEAR_040 | 2.82 | 2570 | -0.021 | 0.376 | -0.398 | 0.20 | 0.402 | 153.3 | 20 | 1403/1122/45 | 1298/1272 | +0.14 | -0.006 | -0.008 |
| B2_NEAR_050 | 2.92 | 2663 | -0.020 | 0.378 | -0.398 | 0.20 | 0.407 | 158.9 | 20 | 1402/1216/45 | 1345/1318 | +0.24 | -0.004 | -0.008 |
| B3_NEAR_070 | 3.31 | 3020 | -0.021 | 0.384 | -0.406 | 0.20 | 0.416 | 183.8 | 20 | 1390/1585/45 | 1520/1500 | +0.63 | -0.006 | -0.017 |
| C0_C_ONLY | 0.05 | 46 | 0.024 | 0.400 | -0.376 | 0.21 | 0.435 | 2.6 | 20 | 0/0/46 | 24/22 | -2.63 | +0.040 | +0.013 |
| C1_C_ONLY_NO_SCORE | 29.31 | 26733 | 0.007 | 0.411 | -0.404 | 0.22 | 0.515 | 1620.2 | 20 | 0/0/26733 | 13551/13182 | +26.63 | +0.023 | -0.015 |

**Combinations**

| variant | trades/day | trades | gross R | cost R | net R | PF | win | max DD % | med hold | A/B/C | L/S | Δ trades/day | Δ gross R | Δ net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1_NEUTRAL_OK+B1_NEAR_040 | 3.97 | 3624 | -0.007 | 0.360 | -0.367 | 0.22 | 0.391 | 199.4 | 20 | 2611/971/42 | 1849/1775 | +1.29 | +0.009 | +0.022 |
| A1_NEUTRAL_OK+B3_NEAR_070 | 4.41 | 4019 | -0.010 | 0.367 | -0.377 | 0.21 | 0.401 | 227.4 | 20 | 2588/1389/42 | 2043/1976 | +1.73 | +0.005 | +0.012 |
| A3_WITHIN3+B1_NEAR_040 | 2.92 | 2660 | -0.019 | 0.375 | -0.394 | 0.20 | 0.403 | 157.2 | 20 | 1507/1108/45 | 1340/1320 | +0.24 | -0.004 | -0.005 |

**TRAIN — net R / trade by family (n)**

| variant | A | B | C | LONG | SHORT |
|---|---|---|---|---|---|
| V11_BASE | -0.384 (1405) | -0.397 (995) | -0.378 (45) | -0.378 (1247) | -0.401 (1198) |
| A1_NEUTRAL_OK | -0.357 (2615) | -0.374 (864) | -0.383 (42) | -0.356 (1807) | -0.367 (1714) |
| A2_WITHIN2 | -0.383 (1443) | -0.395 (993) | -0.378 (45) | -0.379 (1264) | -0.397 (1217) |
| A3_WITHIN3 | -0.382 (1509) | -0.392 (981) | -0.378 (45) | -0.376 (1289) | -0.396 (1246) |
| B1_NEAR_040 | -0.384 (1403) | -0.415 (1122) | -0.378 (45) | -0.387 (1298) | -0.408 (1272) |
| B2_NEAR_050 | -0.384 (1402) | -0.415 (1216) | -0.378 (45) | -0.384 (1345) | -0.411 (1318) |
| B3_NEAR_070 | -0.385 (1390) | -0.424 (1585) | -0.378 (45) | -0.397 (1520) | -0.414 (1500) |
| C0_C_ONLY | - (0) | - (0) | -0.376 (46) | -0.417 (24) | -0.331 (22) |
| C1_C_ONLY_NO_SCORE | - (0) | - (0) | -0.404 (26733) | -0.403 (13551) | -0.405 (13182) |
| A1_NEUTRAL_OK+B1_NEAR_040 | -0.357 (2611) | -0.393 (971) | -0.383 (42) | -0.361 (1849) | -0.373 (1775) |
| A1_NEUTRAL_OK+B3_NEAR_070 | -0.357 (2588) | -0.415 (1389) | -0.383 (42) | -0.374 (2043) | -0.381 (1976) |
| A3_WITHIN3+B1_NEAR_040 | -0.382 (1507) | -0.411 (1108) | -0.378 (45) | -0.385 (1340) | -0.404 (1320) |

## VALIDATION

**A / B / C independent variants**

| variant | trades/day | trades | gross R | cost R | net R | PF | win | max DD % | med hold | A/B/C | L/S | Δ trades/day | Δ gross R | Δ net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V11_BASE | 2.67 | 974 | -0.051 | 0.373 | -0.424 | 0.18 | 0.430 | 62.1 | 20 | 515/438/21 | 493/481 | +0.00 | +0.000 | +0.000 |
| A1_NEUTRAL_OK | 3.48 | 1272 | -0.030 | 0.356 | -0.386 | 0.20 | 0.424 | 73.8 | 20 | 866/386/20 | 656/616 | +0.82 | +0.022 | +0.038 |
| A2_WITHIN2 | 2.71 | 988 | -0.048 | 0.371 | -0.419 | 0.18 | 0.430 | 62.3 | 20 | 531/436/21 | 498/490 | +0.04 | +0.003 | +0.005 |
| A3_WITHIN3 | 2.77 | 1010 | -0.043 | 0.369 | -0.412 | 0.18 | 0.432 | 62.6 | 20 | 556/433/21 | 511/499 | +0.10 | +0.008 | +0.012 |
| B1_NEAR_040 | 2.82 | 1031 | -0.036 | 0.376 | -0.412 | 0.18 | 0.441 | 63.8 | 20 | 513/497/21 | 519/512 | +0.16 | +0.016 | +0.012 |
| B2_NEAR_050 | 2.91 | 1061 | -0.037 | 0.378 | -0.415 | 0.18 | 0.443 | 66.1 | 20 | 510/530/21 | 530/531 | +0.24 | +0.015 | +0.009 |
| B3_NEAR_070 | 3.36 | 1225 | -0.030 | 0.384 | -0.414 | 0.18 | 0.454 | 76.3 | 20 | 511/693/21 | 600/625 | +0.69 | +0.021 | +0.010 |
| C0_C_ONLY | 0.06 | 21 | -0.218 | 0.361 | -0.579 | 0.10 | 0.381 | 1.8 | 20 | 0/0/21 | 9/12 | -2.61 | -0.167 | -0.155 |
| C1_C_ONLY_NO_SCORE | 31.16 | 11372 | 0.012 | 0.419 | -0.407 | 0.21 | 0.531 | 694.4 | 20 | 0/0/11372 | 5774/5598 | +28.49 | +0.063 | +0.017 |

**Combinations**

| variant | trades/day | trades | gross R | cost R | net R | PF | win | max DD % | med hold | A/B/C | L/S | Δ trades/day | Δ gross R | Δ net R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1_NEUTRAL_OK+B1_NEAR_040 | 3.61 | 1317 | -0.022 | 0.359 | -0.381 | 0.20 | 0.429 | 75.5 | 20 | 861/436/20 | 676/641 | +0.94 | +0.029 | +0.043 |
| A1_NEUTRAL_OK+B3_NEAR_070 | 4.08 | 1491 | -0.020 | 0.367 | -0.387 | 0.20 | 0.439 | 86.7 | 20 | 859/612/20 | 748/743 | +1.42 | +0.032 | +0.037 |
| A3_WITHIN3+B1_NEAR_040 | 2.92 | 1066 | -0.029 | 0.372 | -0.401 | 0.19 | 0.442 | 64.3 | 20 | 554/491/21 | 536/530 | +0.25 | +0.022 | +0.023 |

**VALIDATION — net R / trade by family (n)**

| variant | A | B | C | LONG | SHORT |
|---|---|---|---|---|---|
| V11_BASE | -0.385 (515) | -0.463 (438) | -0.579 (21) | -0.389 (493) | -0.460 (481) |
| A1_NEUTRAL_OK | -0.355 (866) | -0.446 (386) | -0.574 (20) | -0.351 (656) | -0.424 (616) |
| A2_WITHIN2 | -0.378 (531) | -0.462 (436) | -0.579 (21) | -0.384 (498) | -0.455 (490) |
| A3_WITHIN3 | -0.367 (556) | -0.462 (433) | -0.579 (21) | -0.378 (511) | -0.446 (499) |
| B1_NEAR_040 | -0.384 (513) | -0.433 (497) | -0.579 (21) | -0.376 (519) | -0.448 (512) |
| B2_NEAR_050 | -0.383 (510) | -0.438 (530) | -0.579 (21) | -0.381 (530) | -0.448 (531) |
| B3_NEAR_070 | -0.383 (511) | -0.433 (693) | -0.579 (21) | -0.378 (600) | -0.449 (625) |
| C0_C_ONLY | - (0) | - (0) | -0.579 (21) | -0.446 (9) | -0.679 (12) |
| C1_C_ONLY_NO_SCORE | - (0) | - (0) | -0.407 (11372) | -0.408 (5774) | -0.406 (5598) |
| A1_NEUTRAL_OK+B1_NEAR_040 | -0.356 (861) | -0.422 (436) | -0.574 (20) | -0.347 (676) | -0.418 (641) |
| A1_NEUTRAL_OK+B3_NEAR_070 | -0.355 (859) | -0.426 (612) | -0.574 (20) | -0.355 (748) | -0.419 (743) |
| A3_WITHIN3+B1_NEAR_040 | -0.366 (554) | -0.433 (491) | -0.579 (21) | -0.367 (536) | -0.436 (530) |

## Family C structural conflict (score model vs RSI exhaustion)

**TRAIN**

| side | C-structure minutes | share of minutes | score p50 / p90 at C structure | share ≥ armed 70 | share ≥ entry 75 | momentum sub-score at C / all minutes | RSI7 exhausted when score ≥ 65 |
|---|---|---|---|---|---|---|---|
| LONG | 99295 | 0.0756 | 36.2 / 49.1 | 0.0007 | 0.0002 | 0.226 / 0.526 | 0.0062 |
| SHORT | 101573 | 0.0773 | 35.6 / 48.8 | 0.0007 | 0.0001 | 0.222 / 0.522 | 0.0062 |

**VALIDATION**

| side | C-structure minutes | share of minutes | score p50 / p90 at C structure | share ≥ armed 70 | share ≥ entry 75 | momentum sub-score at C / all minutes | RSI7 exhausted when score ≥ 65 |
|---|---|---|---|---|---|---|---|
| LONG | 47392 | 0.0902 | 36.2 / 49.4 | 0.0006 | 0.0001 | 0.230 / 0.523 | 0.0065 |
| SHORT | 48129 | 0.0916 | 35.9 / 49.1 | 0.0007 | 0.0001 | 0.228 / 0.513 | 0.0089 |

## Owner selection rule (mechanical)

| variant | TRAIN 3-8/day | TRAIN gross>0 | TRAIN net>0 | TRAIN PF>1 | VAL 3-8/day | VAL gross>0 | VAL net>0 | VAL PF>1 |
|---|---|---|---|---|---|---|---|---|
| V11_BASE | no | no | no | no | no | no | no | no |
| A1_NEUTRAL_OK | yes | no | no | no | yes | no | no | no |
| A2_WITHIN2 | no | no | no | no | no | no | no | no |
| A3_WITHIN3 | no | no | no | no | no | no | no | no |
| B1_NEAR_040 | no | no | no | no | no | no | no | no |
| B2_NEAR_050 | no | no | no | no | no | no | no | no |
| B3_NEAR_070 | yes | no | no | no | yes | no | no | no |
| C0_C_ONLY | no | yes | no | no | no | no | no | no |
| C1_C_ONLY_NO_SCORE | no | yes | no | no | no | yes | no | no |
| A1_NEUTRAL_OK+B1_NEAR_040 | yes | no | no | no | yes | no | no | no |
| A1_NEUTRAL_OK+B3_NEAR_070 | yes | no | no | no | yes | no | no | no |
| A3_WITHIN3+B1_NEAR_040 | no | no | no | no | no | no | no | no |

