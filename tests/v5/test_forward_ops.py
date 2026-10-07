from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
from btc_swing.v5.forward.freeze import write_freeze
from btc_swing.v5.forward.ops import (
    export_state,
    health,
    integrity_compare,
    integrity_snapshot,
    manifest_path_for,
    status_text,
    verify_state,
)
from btc_swing.v5.forward.pipeline import ForwardContext
from btc_swing.v5.forward.raw import RawProcessor

ROOT = Path(__file__).resolve().parents[2]


def _ctx(tmp: Path) -> ForwardContext:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    cfg = load_frozen_v5(fcfg, ROOT)
    start = int(datetime(2026, 10, 7, 15, 0, tzinfo=UTC).timestamp() * 1000)
    return ForwardContext(fcfg, cfg, ForwardPaths(tmp, "BTCUSDT"), start)


def _seed_state(paths: ForwardPaths) -> None:
    (paths.raw / "2026-10-07").mkdir(parents=True, exist_ok=True)
    (paths.raw / "2026-10-07" / "15.jsonl").write_text(
        '{"ts_received_ms": 1, "ts_exchange_ms": 1, "channel": "x", "raw": "{}", "sha256": "a"}\n'
    )
    (paths.raw / "state.json").write_text(
        json.dumps(
            {
                "last_orderbook_u": {"orderbook.50.BTCUSDT": 5},
                "saved_at": "2026-10-07T15:00:00+00:00",
                "stats": {"sequence_gaps": 0, "duplicates": 0},
            }
        )
    )
    paths.signals_file.write_text(
        '{"signal_id": "A|LONG|1", "t_ms": 1}\n{"signal_id": "B|SHORT|2", "t_ms": 2}\n'
    )
    paths.outcomes_file.write_text('{"signal_id": "A|LONG|1", "t_ms": 1}\n')
    paths.paper_trades.write_text(
        '{"family": "A", "side": "LONG", "entry_ms": 1, "POSITION_PNL": 1.0}\n'
    )
    paths.paper_state.write_text(json.dumps({"closed_trades": 1}))
    paths.processor_state.write_text(
        json.dumps(
            {
                "file": "2026-10-07/15.jsonl",
                "line": 1,
                "bars": 1,
                "last_bar_close_ms": 1791385500000,
            }
        )
    )
    paths.cycle_log.write_text(
        '{"cycle_at": "2026-10-07T15:05:20+00:00", "last_bar_close": "2026-10-07T15:05:00+00:00"}\n'
    )
    (paths.seed / "days").mkdir(exist_ok=True)
    (paths.seed / "days" / "2026-10-06.parquet").write_bytes(b"PAR1")
    paths.seed_manifest.write_text('{"day": "2026-10-06"}\n')


def test_export_verify_integrity_roundtrip(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    ctx = _ctx(src)
    _seed_state(ctx.paths)
    freeze = tmp_path / "manifests" / "v5_forward_freeze.json"
    write_freeze(ctx.cfg, ctx.fcfg, freeze)
    cfgs = [ROOT / "config" / "btc_swing_v5_forward.yaml", ROOT / "config" / "btc_swing_v5.yaml"]
    before = integrity_snapshot(ctx)
    out = tmp_path / "export" / "state.tar.gz"
    man = export_state(ctx, out, freeze, cfgs)
    assert man["n_files"] >= 12 and out.exists() and man["integrity"]["signals"]["lines"] == 2
    # extract on the "target"
    import tarfile

    with tarfile.open(out) as tar:
        tar.extractall(dst, filter="data")
    (dst / "btc").mkdir()
    (dst / "forward").rename(dst / "btc" / "forward")
    res = verify_state(dst, manifest_path_for(out), freeze, ROOT / "config")
    assert res["ok"], res
    # continuation: append one signal on the target, integrity must still pass
    ctx2 = _ctx(dst)
    with ctx2.paths.signals_file.open("a") as f:
        f.write('{"signal_id": "C|LONG|3", "t_ms": 3}\n')
    after = integrity_snapshot(ctx2)
    cmp = integrity_compare(before, after, ctx2)
    assert cmp["ok"], [c for c in cmp["checks"] if not c["ok"]]
    # a rewritten earlier line or a duplicate is detected
    ctx2.paths.signals_file.write_text(
        '{"signal_id": "A|LONG|1", "t_ms": 1}\n{"signal_id": "A|LONG|1", "t_ms": 1}\n{"signal_id": "C|LONG|3", "t_ms": 3}\n'
    )
    bad = integrity_compare(before, integrity_snapshot(ctx2), ctx2)
    assert not bad["ok"]
    # tampering with a migrated file fails verification
    ctx2.paths.paper_trades.write_text("tampered\n")
    assert not verify_state(dst, manifest_path_for(out), freeze, ROOT / "config")["ok"]


def test_health_and_status_text_without_runner(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_state(ctx.paths)
    h = health(ctx, tmp_path, min_free_gb=0.0)
    assert not h["ok"] and any("not alive" in p for p in h["problems"]) and h["disk"]["free_gb"] > 0
    txt = status_text(ctx, tmp_path)
    assert (
        "runner" in txt
        and "DEAD" in txt
        and "frozen V5 config hash" in txt
        and ctx.cfg.config_hash in txt
    )


def test_processor_offsets_are_relative_and_legacy_keys_resume(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    p = RawProcessor(raw, tmp_path / "bars", tmp_path / "state.json")
    assert p._key(raw / "2026-10-07" / "15.jsonl.gz") == "2026-10-07/15.jsonl"
    assert (
        p._legacy_key("/home/x/data/btc/forward/bybit/BTCUSDT/2026-10-07/15.jsonl")
        == "2026-10-07/15.jsonl"
    )
    assert (
        p._legacy_key("data/btc/forward/bybit/BTCUSDT/2026-10-07/15.jsonl") == "2026-10-07/15.jsonl"
    )
    assert p._legacy_key("2026-10-07/15.jsonl") == "2026-10-07/15.jsonl"
