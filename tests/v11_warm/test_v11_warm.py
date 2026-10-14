"""V11-WARM: same strategy as V11, PIT-safe warm-start. Synthetic data only; the committed bootstrap
parquet is read for its hash check; no network; nothing under a V10 / V11 forward root is touched."""

from __future__ import annotations

import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from btc_swing.v11.config import load_v11_config
from btc_swing.v11.engine import MS_1M
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.live import V11Live
from btc_swing.v11.paths import V11Paths
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_warm.bootstrap import (
    BAR_KEYS,
    bars_sha256,
    bootstrap_days,
    gapfill_klines,
    load_bootstrap,
    write_bootstrap_bars,
)
from btc_swing.v11_warm.config import (
    BOOTSTRAP_MANIFEST,
    BOOTSTRAP_PARQUET,
    STRATEGY,
    frozen_v11_strategy_hash,
    load_v11w_config,
)
from btc_swing.v11_warm.freeze import check_freeze, write_freeze
from btc_swing.v11_warm.live import V11WarmLive
from btc_swing.v11_warm.paths import V11WarmPaths

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "btc_swing_v11_warm.yaml"
DAY_MS = 86_400_000
T0 = 1_800_000_000_000 - (1_800_000_000_000 % DAY_MS)
P0 = 50_000.0


def synth_rows(days: int, seed: int = 1, t0: int = T0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 1440
    r = rng.normal(0, 0.0006, n) + 0.0004 * np.sin(np.arange(n) / 300.0)
    c = P0 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0003, n)))
    v = rng.lognormal(0, 0.6, n) * (1 + np.abs(r) * 800)
    b = v * rng.uniform(0.3, 0.7, n)
    return pl.DataFrame(
        {
            "open_time_ms": t0 + np.arange(n) * MS_1M,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "n_trades": np.full(n, 30.0),
            "buy_qty": b,
            "sell_qty": v - b,
        }
    )


def _bars_df(rows: pl.DataFrame) -> pl.DataFrame:
    return rows.select(
        pl.col("open_time_ms").alias("open_ms"),
        pl.col("open").alias("o"),
        pl.col("high").alias("h"),
        pl.col("low").alias("l"),
        pl.col("close").alias("c"),
        pl.col("volume").alias("vol"),
        pl.col("buy_qty").alias("buy"),
        pl.col("sell_qty").alias("sell"),
        pl.col("n_trades").cast(pl.Int64).alias("n"),
    )


def _feed(live: V11Live, rows: pl.DataFrame, i0: int, i1: int) -> int:
    """Feed rows [i0, i1) as trades (one buy + one sell per bar reproducing o/h/l/c, vol split)."""
    t = 0
    for r in rows[i0:i1].iter_rows(named=True):
        t = int(r["open_time_ms"])
        n = max(int(r["n_trades"]), 4)
        qb, qs = float(r["buy_qty"]) / (n // 2), float(r["sell_qty"]) / (n - n // 2)
        pxs = [r["open"], r["high"], r["low"], r["close"]]
        data = []
        for k in range(n):
            px = pxs[k] if k < 4 else r["close"]
            buy = k % 2 == 0
            data.append(
                {
                    "T": t + k * (59_000 // n),
                    "s": "BTCUSDT",
                    "S": "Buy" if buy else "Sell",
                    "v": f"{qb if buy else qs:.6f}",
                    "p": f"{px:.1f}",
                    "i": f"{t}-{k}",
                }
            )
        live.on_event(
            {
                "topic": "publicTrade.BTCUSDT",
                "type": "snapshot",
                "ts": t,
                "rx_ms": t + 40,
                "data": data,
            }
        )
    return t + MS_1M


# ----------------------------------------------------------------------------- identity with V11
def test_config_is_the_frozen_v11_strategy() -> None:
    wc, v11 = load_v11w_config(CFG)
    ref = load_v11_config(ROOT / "config" / "btc_swing_v11.yaml")
    assert wc.strategy_name == STRATEGY
    assert v11.strategy_hash == ref.strategy_hash == frozen_v11_strategy_hash()
    assert v11.canonical_yaml() == ref.canonical_yaml()
    assert wc.warm.window_days > v11.features.rvol_baseline_days + 1
    assert wc.warm.bootstrap_days >= v11.features.rvol_baseline_days + 1


def test_identical_decisions_on_the_same_warmed_history(tmp_path: Path) -> None:
    """V11 (cold loader) and V11-WARM (warm loader) given the same fully warmed bar history produce
    identical indicators, scores and decisions on every live bar."""
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(22)
    n_hist = (
        21 * 1440
    )  # inside both live windows (V11 22 d, V11-WARM 31 d): identical rolling frames
    hist = _bars_df(rows[:n_hist])
    out = []
    for cls in ("v11", "warm"):
        if cls == "v11":
            paths: V11Paths = V11Paths(tmp_path / "a", "BTCUSDT", verification=True)
            paths.ensure()
            write_bootstrap_bars(hist, paths.bars_1m)
            live: V11Live = V11Live(
                v11, paths, observation_start_ms=int(rows["open_time_ms"][n_hist])
            )
        else:
            wpaths = V11WarmPaths(tmp_path / "b", "BTCUSDT", verification=True)
            wpaths.ensure()
            write_bootstrap_bars(hist, wpaths.bars_1m)
            live = V11WarmLive(
                v11,
                wpaths,
                int(rows["open_time_ms"][n_hist]),
                wc.warm.model_copy(update={"live_warmup_min": 0}),
            )
        recs, feats, scores = [], [], []
        for i in range(n_hist, n_hist + 240):
            t_end = _feed(live, rows, i, i + 1)
            rec = live.tick(t_end + 1_000)
            recs.append(
                None
                if rec is None
                else {
                    k: rec.get(k) for k in ("signal_id", "action", "family", "side", "stop", "qty")
                }
            )
            feats.append(dict(live.last_features))
            scores.append({k: live.last_scores.get(k) for k in ("long", "short")})
        out.append((recs, feats, scores))
    (ra, fa, sa), (rb, fb, sb) = out
    assert ra == rb and sa == sb
    for x, y in zip(fa, fb, strict=True):
        y = {k: v for k, v in y.items() if k != "atr1h"}  # extra diagnostic key of V11-WARM only
        assert x.keys() == y.keys()
        for k in x:
            vx, vy = x[k], y[k]
            assert (vx is None and vy is None) or (
                vx is not None and vy is not None and math.isclose(vx, vy, rel_tol=0, abs_tol=0)
            ), k


def test_warm_frame_equals_historical_frame_on_identical_bars() -> None:
    _wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(25)
    fr = build_frame_v11(rows, v11)
    live_cfg = v11.model_copy(
        update={"features": v11.features.model_copy(update={"binance_latency_s": 0})}
    )
    fr2 = build_frame_v11(rows, live_cfg)
    i = len(fr) - 1
    for k in (
        "ema50",
        "rsi7",
        "rsi14",
        "adx14",
        "rvol5",
        "vol_tercile",
        "atr5",
        "atr1h",
        "vwap",
        "struct15",
        "hi60",
    ):
        assert np.isfinite(fr.c[k][i]) and fr.c[k][i] == fr2.c[k][i], k
    sc = score_frame(fr, v11)
    assert (
        np.isfinite(sc["score_long"][i]) and sc["avail_weight_long"][i] == 75.0
    )  # no OI/funding here


# ----------------------------------------------------------------------------- bootstrap / PIT
def test_bootstrap_bars_committed_match_manifest_and_are_complete() -> None:
    man = json.loads(BOOTSTRAP_MANIFEST.read_text())
    df = load_bootstrap(BOOTSTRAP_PARQUET)
    assert bars_sha256(df) == man["sha256_bars"]
    assert df.height == man["bars"] == 30 * 1440 and man["days_unavailable"] == []
    assert list(df.columns) == list(BAR_KEYS)
    assert int(df["open_ms"][-1]) + MS_1M == man["last_close_ms"]
    assert (df["open_ms"].diff().drop_nulls() == MS_1M).all()  # contiguous, no fabricated bar
    assert (df["n"] > 0).all() and (df["buy"] + df["sell"] - df["vol"]).abs().max() < 1e-6
    # exactly the 30 days before the end day: strictly before any future observation_start
    assert bootstrap_days(__import__("datetime").date(2026, 10, 13), 30)[0] == "2026-09-13"


def test_seed_refuses_pit_violation_and_reseeding(tmp_path: Path) -> None:
    df = _bars_df(synth_rows(2))
    bars = tmp_path / "bars_1m.jsonl"
    with pytest.raises(RuntimeError):
        write_bootstrap_bars(df, bars, observation_start_ms=int(df["open_ms"][-1]))  # closes after
    n = write_bootstrap_bars(df, bars, observation_start_ms=int(df["open_ms"][-1]) + MS_1M)
    assert n == df.height
    first = json.loads(bars.read_text().splitlines()[0])
    assert first["src"] == "bootstrap" and set(BAR_KEYS) <= set(first)
    with pytest.raises(RuntimeError):
        write_bootstrap_bars(df, bars)


def test_gapfill_uses_public_klines_without_fabricating_taker_split(tmp_path: Path) -> None:
    import httpx

    t0 = (int(__import__("time").time() * 1000) // MS_1M) * MS_1M - 10 * MS_1M
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        start, end = int(req.url.params["start"]), int(req.url.params["end"])
        lst = [
            [str(t), "100", "101", "99", "100.5", "7.5", "750"]
            for t in range(start, end + 1, MS_1M)
        ][::-1]
        return httpx.Response(200, json={"retCode": 0, "result": {"list": lst}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bars = tmp_path / "bars_1m.jsonl"
    n, meta = gapfill_klines("BTCUSDT", t0, bars, 48, None, client=client, base_url="https://mock")
    assert n == 10 and meta["bars"] == 10
    recs = [json.loads(x) for x in bars.read_text().splitlines()]
    assert all(
        r["src"] == "kline_gapfill" and r["buy"] is None and r["sell"] is None and r["n"] == 1
        for r in recs
    )
    assert recs[0]["o"] == 100.0 and recs[0]["vol"] == 7.5
    # never reaches a known observation_start, and refuses an over-long gap
    n2, _ = gapfill_klines(
        "BTCUSDT",
        t0,
        tmp_path / "b2.jsonl",
        48,
        t0 + 3 * MS_1M,
        client=client,
        base_url="https://mock",
    )
    assert n2 == 3
    with pytest.raises(RuntimeError):
        gapfill_klines(
            "BTCUSDT",
            t0 - 49 * 3_600_000,
            tmp_path / "b3.jsonl",
            48,
            None,
            client=client,
            base_url="https://mock",
        )


def test_warm_live_populates_rvol_and_vol_tercile_on_first_tick(tmp_path: Path) -> None:
    """With 25 days of bootstrap bars the pre-declared fields are populated on the FIRST live bar."""
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26)
    n_hist = 25 * 1440
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    live = V11WarmLive(v11, paths, int(rows["open_time_ms"][n_hist]) + 30 * MS_1M, wc.warm)
    assert live.n_bootstrap == n_hist and len(live.micro.bars.done) == n_hist
    for i in range(n_hist, n_hist + 20):  # VWAP needs 15 minutes of the new day
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
    w = live.warm_check()
    assert w["all_fields"] and w["all_groups"], w
    assert live.last_features["rvol5"] is not None and live.last_features["vol_tercile"] is not None
    assert live.last_scores["contrib_long"]["volume"] is not None
    # microstructure is forward-only: its normalisation histories hold only the live samples
    assert all(v < 180 for v in live.micro.history_state().values())
    d = live.diagnostic()
    assert d["strategy"] == STRATEGY and d["in_window"] is False  # live warm-up not elapsed
    json.dumps(d, default=str)


def test_engine_is_not_in_window_before_live_warmup(tmp_path: Path) -> None:
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(24)
    n_hist = 23 * 1440
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    obs = int(rows["open_time_ms"][n_hist]) + 30 * MS_1M
    live = V11WarmLive(v11, paths, obs, wc.warm)
    live.engine = None
    seen_in_window = []
    for i in range(n_hist, n_hist + 40):
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
        lb = live.state.get("last_bar_ms")
        seen_in_window.append(bool(lb is not None and lb > obs))
    assert seen_in_window[:29] == [False] * 29 and seen_in_window[-1] is True
    assert not paths.candidates.exists() or all(
        json.loads(x)["data"]["ts_ms"] > obs
        for x in paths.candidates.read_text().splitlines()
        if x.strip()
    )


# ----------------------------------------------------------------------------- freeze / isolation
def test_freeze_pins_v11_strategy_and_bootstrap(tmp_path: Path) -> None:
    wc, v11 = load_v11w_config(CFG)
    fz = tmp_path / "freeze.json"
    rec = write_freeze(wc, v11, None, fz)
    assert rec["v11_strategy_hash"] == v11.strategy_hash == rec["v11_freeze_strategy_hash"]
    assert rec["observation_start_ms"] == rec["frozen_ms"] + wc.warm.live_warmup_min * MS_1M
    assert (
        rec["bootstrap"]["sha256_bars"] == json.loads(BOOTSTRAP_MANIFEST.read_text())["sha256_bars"]
    )
    assert rec["bootstrap"]["last_close_ms"] < rec["frozen_ms"]
    assert "Demo" in rec["mode"] and "no exchange client" in rec["mode"]
    assert check_freeze(wc, v11, fz)["frozen_at"] == rec["frozen_at"]
    assert write_freeze(wc, v11, None, fz)["frozen_at"] == rec["frozen_at"]  # never overwritten
    raw = yaml.safe_load((ROOT / "config" / "btc_swing_v11.yaml").read_text())
    raw["thresholds"]["entry"] = 76
    p = tmp_path / "v11_mod.yaml"
    p.write_text(yaml.safe_dump(raw))
    wraw = yaml.safe_load(CFG.read_text())
    wraw["v11_config"] = str(p)
    wp = tmp_path / "warm_mod.yaml"
    wp.write_text(yaml.safe_dump(wraw))
    with pytest.raises(RuntimeError):  # drift from the V11 freeze is refused at load time
        load_v11w_config(wp)


def test_isolation_from_v10_and_v11(tmp_path: Path) -> None:
    src = "".join(p.read_text() for p in sorted((ROOT / "btc_swing" / "v11_warm").glob("*.py")))
    for tok in (
        "btc_swing.v10.execution",
        "btc_swing.v10.activation",
        "btc_swing.v10.paths",
        "btc_swing.v10.engine",
        "btc_swing.v10.shadow",
        "btc_swing.v11.freeze",
        "btc_swing.v11.runner",
        "btc_swing.v5.demo.client",
        "BybitDemoClient",
        "BYBIT_DEMO_API",
        "api-demo",
        "place_order",
        "create_order",
        "forward_v10",
        "STRATEGY_DEMO",
        "V10D-",
        "V11D-",
    ):
        assert tok not in src, f"V11-WARM source references {tok}"
    assert "forward_v11/" not in src and '"forward_v11"' not in src
    paths = V11WarmPaths(tmp_path, "BTCUSDT")
    assert (
        paths.root.name == "forward_v11_warm" and paths.lock != V11Paths(tmp_path, "BTCUSDT").lock
    )
    assert "forward_v11_warm_verify" in str(
        V11WarmPaths(tmp_path, "BTCUSDT", verification=True).root
    )
    wc, v11 = load_v11w_config(CFG)
    live = V11WarmLive(v11, paths, T0, wc.warm)
    live.save()
    written = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    assert written and all(w.startswith("btc/forward_v11_warm/") for w in written)
    # the committed V11 freeze and config are untouched by loading / freezing V11-WARM
    v11_fz, v11_cfg = (
        ROOT / "manifests" / "v11_forward_freeze.json",
        ROOT / "config" / "btc_swing_v11.yaml",
    )
    before = tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (v11_fz, v11_cfg))
    write_freeze(wc, v11, None, tmp_path / "fz.json")
    assert tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (v11_fz, v11_cfg)) == before


def test_warm_tick_keeps_detecting_bars_when_the_window_is_full(tmp_path: Path) -> None:
    """The deque is full once window_days of bars exist; V11-WARM detects new bars by open time."""
    wc, v11 = load_v11w_config(CFG)
    warm = wc.warm.model_copy(update={"window_days": 22, "live_warmup_min": 0})
    rows = synth_rows(23)
    n_hist = 22 * 1440  # exactly the deque size: every later bar rolls the oldest out
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    live = V11WarmLive(v11, paths, int(rows["open_time_ms"][n_hist]), warm)
    assert len(live.micro.bars.done) == live.micro.bars.done.maxlen == n_hist
    ticks = 0
    for i in range(n_hist, n_hist + 25):
        t_end = _feed(live, rows, i, i + 1)
        if live.tick(t_end + 1_000) is not None or live.state.get("last_bar_ms"):
            ticks += 1
    assert live.state["last_bar_ms"] == int(rows["open_time_ms"][n_hist + 23]) + MS_1M
    assert ticks >= 20 and len(live.micro.bars.done) == n_hist


def test_gapfilled_seed_is_warm_on_the_first_live_bar(tmp_path: Path) -> None:
    """Bootstrap bars + kline gap-fill bars up to the runner start: every pre-declared field and
    group is populated on the first live bar (taker split of gap-fill bars unknown, declared)."""
    import httpx

    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26, t0=T0 - 120 * DAY_MS)  # in the past: the gap-fill end is bounded by now
    n_boot, n_gap = 25 * 1440, 6 * 60  # archive to midnight, 6 h of klines, then live bars
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_boot]), paths.bars_1m)

    def handler(req: httpx.Request) -> httpx.Response:
        start, end = int(req.url.params["start"]), int(req.url.params["end"])
        lst = []
        for r in rows[n_boot : n_boot + n_gap].iter_rows(named=True):
            t = int(r["open_time_ms"])
            if start <= t <= end:
                lst.append(
                    [
                        str(t),
                        str(r["open"]),
                        str(r["high"]),
                        str(r["low"]),
                        str(r["close"]),
                        str(r["volume"]),
                        "0",
                    ]
                )
        return httpx.Response(200, json={"retCode": 0, "result": {"list": lst[::-1][:1000]}})

    gap_from = int(rows["open_time_ms"][n_boot])
    obs = int(rows["open_time_ms"][n_boot + n_gap])
    n, _meta = gapfill_klines(
        "BTCUSDT",
        gap_from,
        paths.bars_1m,
        48,
        obs,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://mock",
    )
    assert n == n_gap
    live = V11WarmLive(v11, paths, obs + 30 * MS_1M, wc.warm)
    assert live.n_bootstrap == n_boot + n_gap
    for i in range(n_boot + n_gap, n_boot + n_gap + 3):
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
    w = live.warm_check()
    assert w["all_fields"], w  # every bar-derived field on the first live bar
    assert live.last_features["atr5"] is not None and live.last_features["atr1h"] is not None
    assert live.last_features["hi60"] is not None and live.last_features["rvol5"] is not None
    # the taker-flow windows (<= 15 min) need live bars: the volume group completes inside the
    # 30-minute live warm-up, before the engine is in-window
    assert w["groups"]["volume"] is False and all(
        v for g, v in w["groups"].items() if g != "volume"
    )
    for i in range(n_boot + n_gap + 3, n_boot + n_gap + 20):
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
    w = live.warm_check()
    assert w["all_fields"] and w["all_groups"], w
    assert live.state["last_bar_ms"] <= obs + 30 * MS_1M  # still inside the live warm-up


# ----------------------------------------------------------------------------- seam healing
def _seed_with_gapfill(tmp_path: Path, rows: pl.DataFrame, n_boot: int, n_gap: int) -> V11WarmPaths:
    """Bootstrap bars then kline gap-fill bars (taker split unknown) exactly as `v11w seed` writes them."""
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_boot]), paths.bars_1m)
    gap = _bars_df(rows[n_boot : n_boot + n_gap]).with_columns(
        pl.lit(None).cast(pl.Float64).alias("buy"),
        pl.lit(None).cast(pl.Float64).alias("sell"),
        pl.lit(1).alias("n"),
    )
    with paths.bars_1m.open("a") as fh:
        for r in gap.iter_rows(named=True):
            fh.write(json.dumps({**r, "src": "kline_gapfill"}) + "\n")
    return paths


def _kline_mock(rows: pl.DataFrame, calls: list[tuple[int, int]] | None = None):
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        start, end = int(req.url.params["start"]), int(req.url.params["end"])
        if calls is not None:
            calls.append((start, end))
        lst = [
            [
                str(r["open_time_ms"]),
                str(r["open"]),
                str(r["high"]),
                str(r["low"]),
                str(r["close"]),
                str(r["volume"]),
                "0",
            ]
            for r in rows.iter_rows(named=True)
            if start <= int(r["open_time_ms"]) <= end
        ]
        return httpx.Response(200, json={"retCode": 0, "result": {"list": lst[::-1][:1000]}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _failing_kline_client():
    import httpx

    return httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(403, text="blocked"))
    )


CONTIGUOUS_FIELDS = ("atr5", "hi60", "vol_tercile", "atr1h")
BAR_GROUPS = ("trend", "momentum", "volatility", "volume", "location")


def test_one_minute_seam_hole_reproduces_the_mac_symptom_without_healing(tmp_path: Path) -> None:
    """A single missing minute between the gap-fill and the first live bar leaves EMA/RSI/ADX/RVOL
    populated (state-carrying) but ATR5 / HI60 / tercile and four groups missing (contiguous windows)."""
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26)
    n_boot, n_gap = 25 * 1440, 6 * 60
    paths = _seed_with_gapfill(tmp_path, rows, n_boot, n_gap)
    start = n_boot + n_gap + 1  # minute n_boot+n_gap is missing: the seam hole
    live = V11WarmLive(
        v11,
        paths,
        int(rows["open_time_ms"][start]),
        wc.warm.model_copy(update={"live_warmup_min": 0}),
    )
    live.kline_client = _failing_kline_client()  # REST unreachable: hole cannot be healed
    for i in range(start, start + 18):
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
    f, w = live.last_features, live.warm_check()
    assert all(
        f[k] is not None for k in ("ema50", "rsi7", "rsi14", "adx14", "rvol5", "vwap", "struct15")
    )
    assert all(f.get(k) is None for k in ("atr5", "hi60", "vol_tercile"))  # contiguous windows
    assert not any(w["groups"][g] for g in ("trend", "momentum", "volatility", "location"))
    assert len(live.holes()) == 1 and live.diagnostic()["warm"]["holes_in_window"] == 1


def test_seam_hole_is_healed_on_first_live_bar_and_first_decision_sees_all_bar_groups(
    tmp_path: Path,
) -> None:
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26)
    n_boot, n_gap = 25 * 1440, 6 * 60
    paths = _seed_with_gapfill(tmp_path, rows, n_boot, n_gap)
    start = n_boot + n_gap + 2  # two missing minutes at the seam (seed minute + agent start minute)
    calls: list[tuple[int, int]] = []
    obs = (
        int(rows["open_time_ms"][start]) + wc.warm.live_warmup_min * MS_1M
    )  # as the freeze sets it
    live = V11WarmLive(v11, paths, obs, wc.warm)
    live.kline_client = _kline_mock(rows, calls)
    assert (
        live.holes() == []
    )  # nothing to heal at startup: the hole appears with the first live bar
    seen: list[tuple[int, dict[str, float | None]]] = []
    for i in range(start, start + wc.warm.live_warmup_min + 3):
        t_end = _feed(live, rows, i, i + 1)
        live.tick(t_end + 1_000)
        if live.engine is not None and live.state.get("last_bar_ms"):
            eng = live.engine
            j = int(np.searchsorted(eng.t_open, int(live.state["last_bar_ms"]) - MS_1M))
            seen.append((int(live.state["last_bar_ms"]), eng._contrib(1, j)))
    assert calls and live.holes() == []
    assert (
        live.state["seam_fills"][0]["bars"] == 2 and live.state["seam_fills"][0]["reason"] == "live"
    )
    f, w = live.last_features, live.warm_check()
    assert all(f.get(k) is not None for k in CONTIGUOUS_FIELDS), f
    assert w["all_fields"] and w["all_groups"], w
    # the FIRST live bar already has the price-derived groups (ATR5 / HI60 / tercile healed) ...
    first = seen[0][1]
    assert all(first[g] is not None for g in ("trend", "momentum", "volatility", "location")), first
    # ... and the FIRST IN-WINDOW decision (after the 30-min live warm-up) has every bar-derived
    # group, including volume, whose taker-flow windows (<= 15 min) cannot come from kline bars
    first_in_window = next(c for t, c in seen if t > obs)
    assert all(first_in_window[g] is not None for g in BAR_GROUPS), first_in_window
    # seam bars are on disk flagged as kline_seamfill, sorted on reload, and the deque is contiguous
    recs = [json.loads(x) for x in paths.bars_1m.read_text().splitlines() if x.strip()]
    assert sum(1 for r in recs if r.get("src") == "kline_seamfill") == 2
    opens = [int(b.open_ms) for b in live.micro.bars.done]
    assert all(b - a == MS_1M for a, b in pairwise(opens))


def test_startup_heals_a_historical_seam_hole(tmp_path: Path) -> None:
    """A root that was installed with a seam hole (the Mac case) is healed when the runner restarts."""
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26)
    n_boot, n_gap = 25 * 1440, 6 * 60
    paths = _seed_with_gapfill(tmp_path, rows, n_boot, n_gap)
    hole = n_boot + n_gap
    # live bars written by the first (unhealed) run, starting one minute after the gap-fill end
    live_rows = _bars_df(rows[hole + 1 : hole + 40])
    with paths.bars_1m.open("a") as fh:
        for r in live_rows.iter_rows(named=True):
            fh.write(json.dumps(r) + "\n")
    import btc_swing.v11_warm.live as live_mod

    orig = live_mod.V11WarmLive.heal_holes

    # inject the mock client before the startup heal runs
    def patched(self, reason):  # type: ignore[no-untyped-def]
        self.kline_client = _kline_mock(rows)
        return orig(self, reason)

    live_mod.V11WarmLive.heal_holes = patched  # type: ignore[method-assign]
    try:
        live = V11WarmLive(
            v11,
            paths,
            int(rows["open_time_ms"][hole + 40]),
            wc.warm.model_copy(update={"live_warmup_min": 0}),
        )
    finally:
        live_mod.V11WarmLive.heal_holes = orig  # type: ignore[method-assign]
    assert live.holes() == [] and live.state["seam_fills"][0]["reason"] == "startup"
    t_end = _feed(live, rows, hole + 40, hole + 42)
    live.tick(t_end + 1_000)
    assert all(live.last_features.get(k) is not None for k in CONTIGUOUS_FIELDS)
    assert live.warm_check()["all_fields"] and live.warm_check()["all_groups"]
    # the bars file is read back sorted and de-duplicated
    live2 = V11WarmLive(v11, paths, int(rows["open_time_ms"][hole + 40]), wc.warm)
    opens = [int(b.open_ms) for b in live2.micro.bars.done]
    assert opens == sorted(set(opens)) and live2.holes() == []


def test_reconnect_hole_mid_run_is_healed(tmp_path: Path) -> None:
    wc, v11 = load_v11w_config(CFG)
    rows = synth_rows(26)
    n_boot = 25 * 1440
    paths = V11WarmPaths(tmp_path, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_boot]), paths.bars_1m)
    live = V11WarmLive(
        v11,
        paths,
        int(rows["open_time_ms"][n_boot]),
        wc.warm.model_copy(update={"live_warmup_min": 0}),
    )
    live.kline_client = _kline_mock(rows)
    for i in range(n_boot, n_boot + 5):
        live.tick(_feed(live, rows, i, i + 1) + 1_000)
    for i in range(n_boot + 9, n_boot + 12):  # minutes n_boot+5..+8 lost to a reconnect
        live.tick(_feed(live, rows, i, i + 1) + 1_000)
    assert live.holes() == [] and live.state["seam_fills"][-1]["bars"] == 4
    assert live.last_features["atr5"] is not None and live.last_features["hi60"] is not None
