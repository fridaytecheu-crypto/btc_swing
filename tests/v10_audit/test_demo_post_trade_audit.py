"""The read-only V10 Demo post-trade audit on a LIVE append-only root: appends during the audit are
ignored and allowed; modifying or truncating bytes that existed at the audit start fails; records after
the cutoff are excluded; nothing under the V10 root is written. Synthetic data only; no network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v10_demo_post_trade_audit.py"
T0 = 1_791_993_600_000  # 2026-10-14 02:00 UTC (fixed; the audit cutoff is passed explicitly)
MS_1M = 60_000


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v10_demo_post_trade_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v10_demo_post_trade_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


AUD = _load()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _jrec(kind: str, data: dict[str, Any], ts_ms: int) -> str:
    return json.dumps({"kind": kind, "data": data, "ts": _iso(ts_ms)}) + "\n"


def build_root(tmp: Path) -> Path:
    """A minimal V10 root: 4 h of trade prints every 10 s, 1m bars, 4 closed Demo trades."""
    root = tmp / "btc" / "forward_v10"
    for sub in ("demo", "journals", "derived"):
        (root / sub).mkdir(parents=True)
    prices = [
        (T0 + k * 10_000, 66_000.0 + 30.0 * ((k // 30) % 7) - 0.5 * (k % 30))
        for k in range(4 * 360)
    ]
    raw = root / "raw" / "BTCUSDT"
    for t, p in prices:
        d = datetime.fromtimestamp(t / 1000, tz=UTC)
        f = raw / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        msg = {
            "topic": "publicTrade.BTCUSDT",
            "ts": t,
            "data": [{"T": t, "p": f"{p:.1f}", "v": "0.01"}],
        }
        with f.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "channel": "publicTrade",
                        "raw": json.dumps(msg),
                        "sha256": "x",
                        "ts_received_ms": t,
                    }
                )
                + "\n"
            )
    with (root / "derived" / "bars_1m.jsonl").open("w") as fh:
        for m in range(4 * 60):
            seg = [p for t, p in prices if T0 + m * MS_1M <= t < T0 + (m + 1) * MS_1M]
            fh.write(
                json.dumps(
                    {
                        "open_ms": T0 + m * MS_1M,
                        "o": seg[0],
                        "h": max(seg),
                        "l": min(seg),
                        "c": seg[-1],
                    }
                )
                + "\n"
            )
    with (
        (root / "demo" / "demo_trades.jsonl").open("w") as ft,
        (root / "journals" / "signals.jsonl").open("w") as fs,
        (root / "journals" / "outcomes.jsonl").open("w") as fo,
        (root / "journals" / "episodes.jsonl").open("w") as fe,
    ):
        for k in range(4):
            t_in = T0 + (30 + 40 * k) * MS_1M
            entry = next(p for t, p in prices if t >= t_in)
            s = 1 if k % 2 == 0 else -1
            stop = entry - s * entry * 0.007
            t_out = t_in + 20 * MS_1M
            xp = entry - s * 20.0
            sid = f"V10S-test{k}"
            ft.write(json.dumps({
                "signal_id": sid, "episode_id": f"E{k}", "family": "ABC"[k % 3], "side": "LONG" if s > 0 else "SHORT",
                "entry_ref": entry, "entry_price": entry + s * 1.0, "stop": stop, "tp1": entry + s * abs(entry - stop),
                "tp2": entry + 2 * s * abs(entry - stop), "qty": 0.016, "planned_risk_usdt": 7.5, "actual_risk_usdt": 7.4,
                "exits": [{"price": xp, "qty": 0.016, "link": "V10O-x-TC", "time": _iso(t_out)}], "exit_reason": "TIME",
                "fees": 1.1, "funding": 0.0, "gross_pnl": -0.3, "net_pnl": -1.4 if k != 1 else 2.0, "R_net_vs_actual": -0.19,
                "decision_ts": _iso(t_in - 500), "fill_ts": _iso(t_in), "closed_ts": _iso(t_out + 2000), "closed_ms": t_out + 2000,
            }) + "\n")  # fmt: skip
            fs.write(
                _jrec(
                    "SIGNAL_EXECUTED",
                    {
                        "signal_id": sid,
                        "episode_id": f"E{k}",
                        "features": {"last": entry, "atr5": 120.0, "imb_10": 0.2 * s},
                    },
                    t_in,
                )
            )
            fe.write(
                _jrec(
                    "EPISODE_ARMED",
                    {"episode_id": f"E{k}", "conditions": {"at_location": True}},
                    t_in - MS_1M,
                )
            )
            for h in (5, 15, 30, 60):
                fo.write(
                    _jrec(
                        "OUTCOME",
                        {"id": sid, "kind": "EXECUTED", "horizon_min": h, "return_bps": 1.0 * h},
                        t_in + h * MS_1M,
                    )
                )
    (root / "demo" / "strategy_journal.jsonl").write_text(
        _jrec("TRADE_CLOSED", {"signal_id": "V10S-test0"}, T0)
    )
    (root / "state.json").write_text(
        json.dumps({"counters": {}})
    )  # rewritten atomically by V10: never read
    return root


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


CUTOFF = T0 + 4 * 3_600_000  # after the last synthetic print


def test_audit_reads_only_and_reports_the_last_trades(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    before = _tree(root)
    md, js, rep = AUD.run_audit(
        root,
        tmp_path / "out",
        6,
        cutoff_ms=CUTOFF,
        config_path=ROOT / "config" / "btc_swing_v10.yaml",
    )
    assert _tree(root) == before  # nothing under the V10 root was written
    assert md.parent == tmp_path / "out" and js.exists()
    assert rep["last_n"] == [4, 3, 2, 1] and rep["integrity"]["prefixes_identical"]
    assert rep["integrity"]["appended_bytes_ignored"] == 0
    t = rep["trades"][0]
    assert (
        t["path_source"] == "raw trades"
        and t["mfe_R"] is not None
        and t["exit_fills"][0]["kind"].startswith("MARKET_CLOSE")
    )
    assert not any("state.json" in k for k in rep["input_prefixes"])


def test_appending_journal_records_during_the_audit_does_not_fail_and_is_ignored(
    tmp_path: Path,
) -> None:
    root = build_root(tmp_path)
    late = T0 + 3 * 3_600_000

    def live_runner_appends() -> None:  # V10 keeps appending while the audit runs
        with (root / "demo" / "demo_trades.jsonl").open("a") as fh:
            fh.write(
                json.dumps(
                    {"signal_id": "V10S-late", "side": "LONG", "net_pnl": 5.0, "closed_ms": late}
                )
                + "\n"
            )
        for name in ("signals", "outcomes", "episodes"):
            with (root / "journals" / f"{name}.jsonl").open("a") as fh:
                fh.write(_jrec("SIGNAL_EXECUTED", {"signal_id": "V10S-late"}, late))
        with (root / "derived" / "bars_1m.jsonl").open("a") as fh:
            fh.write(json.dumps({"open_ms": late, "o": 1, "h": 1, "l": 1, "c": 1}) + "\n")
        raw_files = sorted((root / "raw" / "BTCUSDT").rglob("*.jsonl"))
        with raw_files[-1].open("a") as fh:
            fh.write('{"partial line still being written')

    _md, _js, rep = AUD.run_audit(
        root, tmp_path / "out", 6, cutoff_ms=CUTOFF, config_path=ROOT / "config" / "btc_swing_v10.yaml",
        during=live_runner_appends,
    )  # fmt: skip
    assert rep["integrity"]["prefixes_identical"] and rep["integrity"]["appended_bytes_ignored"] > 0
    assert "V10S-late" not in {t["signal_id"] for t in rep["trades"]}  # appended after the snapshot
    assert len(rep["trades"]) == 4


def test_partial_last_line_at_audit_start_is_ignored(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    with (root / "demo" / "demo_trades.jsonl").open("a") as fh:
        fh.write('{"signal_id": "V10S-half", "closed_ms": 1')  # writer mid-line at the cutoff
    _md, _js, rep = AUD.run_audit(
        root,
        tmp_path / "out",
        6,
        cutoff_ms=CUTOFF,
        config_path=ROOT / "config" / "btc_swing_v10.yaml",
    )
    assert len(rep["trades"]) == 4


@pytest.mark.parametrize(
    "target", ["demo/demo_trades.jsonl", "journals/signals.jsonl", "derived/bars_1m.jsonl", "RAW"]
)
def test_modifying_existing_bytes_during_the_audit_fails(tmp_path: Path, target: str) -> None:
    root = build_root(tmp_path)
    path = (
        sorted((root / "raw" / "BTCUSDT").rglob("*.jsonl"))[0] if target == "RAW" else root / target
    )

    def tamper() -> None:  # same length, one byte changed inside the recorded prefix
        b = bytearray(path.read_bytes())
        i = b.index(b"1")
        b[i : i + 1] = b"2"
        path.write_bytes(bytes(b))

    with pytest.raises(AUD.SnapshotIntegrityError, match="modified"):
        AUD.run_audit(
            root,
            tmp_path / "out",
            6,
            cutoff_ms=CUTOFF,
            config_path=ROOT / "config" / "btc_swing_v10.yaml",
            during=tamper,
        )


@pytest.mark.parametrize("target", ["demo/demo_trades.jsonl", "journals/outcomes.jsonl"])
def test_truncation_during_the_audit_fails(tmp_path: Path, target: str) -> None:
    root = build_root(tmp_path)
    path = root / target

    def truncate() -> None:
        b = path.read_bytes()
        path.write_bytes(b[: len(b) // 2])

    with pytest.raises(AUD.SnapshotIntegrityError, match="truncated"):
        AUD.run_audit(
            root,
            tmp_path / "out",
            6,
            cutoff_ms=CUTOFF,
            config_path=ROOT / "config" / "btc_swing_v10.yaml",
            during=truncate,
        )


def test_deletion_during_the_audit_fails(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    with pytest.raises(AUD.SnapshotIntegrityError, match="deleted"):
        AUD.run_audit(
            root, tmp_path / "out", 6, cutoff_ms=CUTOFF, config_path=ROOT / "config" / "btc_swing_v10.yaml",
            during=lambda: (root / "journals" / "signals.jsonl").unlink(),
        )  # fmt: skip


def test_trades_closed_after_the_cutoff_are_excluded(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    cutoff = T0 + (30 + 40 * 2 + 21) * MS_1M  # after trade 3 closed, before trade 4 closed
    _md, _js, rep = AUD.run_audit(
        root,
        tmp_path / "out",
        6,
        cutoff_ms=cutoff,
        config_path=ROOT / "config" / "btc_swing_v10.yaml",
    )
    assert [t["signal_id"] for t in rep["trades"]] == ["V10S-test0", "V10S-test1", "V10S-test2"]
    last = rep["trades"][-1]
    assert last["fwd_60m_bps_from_fill"] is None  # +60 min lies beyond the cutoff
    assert last["post_exit_window_complete"] is False
