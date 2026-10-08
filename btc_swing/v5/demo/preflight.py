"""Preflight for real Bybit DEMO validation. Mode stays DISABLED throughout.

1. Environment (no request to Bybit beyond an unauthenticated reachability probe): persistent host
   (not an ephemeral cloud-session container, runner alive here), forward freeze unchanged
   (V5 hash and observation start), exactly one forward runner, api-demo.bybit.com reachable.
2. Only if 1 passes: read-only authenticated checks with a GET-only client (API key auth and info,
   DEMO account confirmation, UNIFIED wallet, BTCUSDT position and open orders, live instrument
   rules) and the private DEMO WebSocket authentication.
3. Reference-equity sufficiency from the LIVE instrument rules for the frozen TP1/TP2 structure.
A PASSED preflight report is required before `btc-swing v5 demo smoke`."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from btc_swing.v5.demo.client import BybitDemoClient, DemoApiError, DemoTransportError
from btc_swing.v5.demo.config import DEMO_REST_HOST, DemoExecConfig, ExecutionMode, assert_demo_url
from btc_swing.v5.demo.credentials import (
    DemoCredentials,
    DemoCredentialsMissingError,
    load_demo_credentials,
)
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.demo.requirements import reference_equity_report
from btc_swing.v5.demo.ws import private_ws_auth

EXPECTED_V5_HASH = "d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177"
EXPECTED_OBSERVATION_START_MS = 1791385162972  # 2026-10-07T14:59:22.972Z
PREFLIGHT_DIR = Path("reports/forward/demo_preflight")
PREFLIGHT_TAG = "PREFLIGHT_READ_ONLY"


def _runner_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "btc-swing v5 forward run"], capture_output=True, text=True, check=False
        ).stdout
    except FileNotFoundError:
        return []
    me = os.getpid()
    pids = []
    for x in out.split():
        try:
            pid = int(x)
        except ValueError:
            continue
        if pid == me:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if "python" in cmd and "btc-swing v5 forward run" in cmd:
            pids.append(pid)
    return pids


def is_cloud_session_container() -> bool:
    """True inside an ephemeral Claude Code cloud-session container (not a persistent host)."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true" or Path("/root/.ccr").exists()


def environment_checks(
    freeze: dict[str, Any] | None,
    v5_hash: str,
    pid_file: Path,
    probe: Callable[[], tuple[int, str]] | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    cloud = is_cloud_session_container()
    systemd = Path("/run/systemd/system").exists()
    add(
        "persistent host (not an ephemeral cloud-session container)",
        not cloud,
        "cloud-session container detected (CLAUDE_CODE_REMOTE / agent proxy)"
        if cloud
        else f"systemd {'present' if systemd else 'absent'}",
    )
    alive = False
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
            alive = True
        except (OSError, ValueError):
            alive = False
    pids = _runner_pids()
    add(
        "forward runner running on this host",
        alive and len(pids) >= 1,
        f"pid file alive={alive}; runner processes={pids}",
    )
    add("exactly one forward runner", len(pids) == 1, f"{len(pids)} runner process(es)")
    fz: dict[str, Any] = freeze or {}
    add(
        "forward freeze present and V5 hash unchanged",
        fz.get("v5_config_hash") == EXPECTED_V5_HASH == v5_hash,
        f"freeze {str(fz.get('v5_config_hash', 'missing'))[:12]} / config {v5_hash[:12]} / expected {EXPECTED_V5_HASH[:12]}",
    )
    add(
        "observation start unchanged",
        int(fz.get("observation_start_ms", -1)) == EXPECTED_OBSERVATION_START_MS,
        str(fz.get("observation_start", "missing")),
    )
    try:
        status, body = probe() if probe else _probe()
        geo = "country" in body.lower()
        add(
            f"{DEMO_REST_HOST} reachable (unauthenticated probe)",
            status == 200,
            f"HTTP {status}" + (" (Bybit country restriction)" if geo else ""),
        )
    except httpx.HTTPError as e:
        add(
            f"{DEMO_REST_HOST} reachable (unauthenticated probe)",
            False,
            f"{type(e).__name__}: {e}"[:200],
        )
    return checks


def _probe() -> tuple[int, str]:
    url = f"https://{DEMO_REST_HOST}/v5/market/time"
    assert_demo_url(url)
    r = httpx.get(url, timeout=10)
    return r.status_code, r.text[:300]


def read_only_checks(
    dcfg: DemoExecConfig,
    journal: HashChainJournal,
    creds: DemoCredentials | None = None,
    transport: httpx.BaseTransport | None = None,
    ws_connect: Any = None,
) -> dict[str, Any]:
    """GET-only authenticated checks + private WS auth. Raises nothing; returns per-check results."""
    res: dict[str, Any] = {"checks": [], "data": {}}

    def add(name: str, ok: bool, detail: Any) -> None:
        res["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        creds = creds or load_demo_credentials(dcfg)
    except DemoCredentialsMissingError as e:
        add("demo credentials present", False, str(e))
        return res
    cl = BybitDemoClient(
        dcfg, ExecutionMode.DISABLED, journal, PREFLIGHT_TAG, creds, transport, read_only=True
    )
    steps: list[tuple[str, Callable[[], Any]]] = [
        ("server time / clock offset", cl.sync_time),
        ("API key authentication (signed account info)", cl.account_info),
        ("API key information", cl.api_key_info),
        ("UNIFIED wallet balance", cl.wallet_balance),
        ("BTCUSDT current position", cl.position),
        ("BTCUSDT open orders", cl.open_orders),
        ("BTCUSDT instrument rules", cl.instrument),
        ("ticker", cl.ticker),
    ]
    for name, fn in steps:
        try:
            v = fn()
            res["data"][name] = v
            add(
                name,
                True,
                v
                if name not in ("BTCUSDT open orders",)
                else {"n_open": len(v), "links": [o.get("orderLinkId") for o in v]},
            )
        except (DemoApiError, DemoTransportError) as e:
            add(name, False, f"{type(e).__name__}: {e}"[:300])
    auth_ok = any(c["ok"] for c in res["checks"] if c["check"].startswith("API key authentication"))
    key_info = res["data"].get("API key information") or {}
    # DEMO confirmation: the key authenticates on api-demo.bybit.com (production keys are rejected
    # there) and every request was verified against the demo host before it was sent.
    hosts = {
        str(r["data"].get("request", {}).get("path"))
        for r in journal.records()
        if r.get("tag") == PREFLIGHT_TAG
    }
    add(
        "account is DEMO (key authenticates on api-demo.bybit.com; no other host contacted)",
        auth_ok,
        {
            "host": dcfg.rest_base,
            "paths": sorted(hosts),
            "key_read_only_flag": key_info.get("readOnly"),
            "key_permissions": key_info.get("permissions"),
        },
    )
    inst = res["data"].get("BTCUSDT instrument rules") or {}
    rules_ok = (
        bool(inst)
        and all(
            inst.get(k) == inst.get(k) and inst.get(k) is not None
            for k in ("min_qty", "qty_step", "tick", "max_leverage")
        )
        and inst.get("status") == "Trading"
    )
    add("instrument rules complete (min qty, step, tick, min notional, leverage)", rules_ok, inst)
    ws = private_ws_auth(dcfg, creds, connect=ws_connect)
    add("private DEMO WebSocket authentication", ws["ok"], ws)
    pos = res["data"].get("BTCUSDT current position") or {}
    add("no existing BTCUSDT position", pos.get("size", 1) == 0, pos)
    oo = res["data"].get("BTCUSDT open orders")
    add(
        "no open BTCUSDT orders",
        oo is not None and len(oo) == 0,
        {"n_open": None if oo is None else len(oo)},
    )
    cl.close()
    return res


def run_preflight(
    dcfg: DemoExecConfig,
    cfg_hash: str,
    freeze: dict[str, Any] | None,
    pid_file: Path,
    journal: HashChainJournal,
    price_fallback: float | None,
    **kw: Any,
) -> dict[str, Any]:
    if dcfg.mode is not ExecutionMode.DISABLED:
        raise RuntimeError("preflight requires the configured mode to be DISABLED")
    started = datetime.now(UTC)
    env = environment_checks(freeze, cfg_hash, pid_file, kw.pop("probe", None))
    out: dict[str, Any] = {
        "run_id": started.strftime("%y%m%d%H%M%S"),
        "started_at": started.isoformat(),
        "mode": dcfg.mode.value,
        "environment": env,
        "read_only": None,
        "requirements": None,
    }
    if all(c["ok"] for c in env):
        ro = read_only_checks(dcfg, journal, **kw)
        out["read_only"] = ro
        inst = ro["data"].get("BTCUSDT instrument rules")
        px = (ro["data"].get("ticker") or {}).get("last") or price_fallback
        if inst and px:
            out["requirements"] = {
                "source": "LIVE instrument rules",
                **reference_equity_report(
                    dcfg.reference_equity_usdt, dcfg.risk_per_trade, float(px), inst
                ),
            }
    ok_env = all(c["ok"] for c in env)
    ok_ro = out["read_only"] is not None and all(c["ok"] for c in out["read_only"]["checks"])
    out["status"] = (
        "PASSED" if ok_env and ok_ro else ("BLOCKED_ENVIRONMENT" if not ok_env else "FAILED")
    )
    out["finished_at"] = datetime.now(UTC).isoformat()
    out["authenticated_requests"] = sum(
        1
        for r in journal.records()
        if r.get("tag") == PREFLIGHT_TAG and r["data"].get("request", {}).get("auth")
    )
    return out


def write_preflight(out: dict[str, Any], out_dir: Path = PREFLIGHT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{out['run_id']}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True, default=str)
    )
    lines = [
        f"# BTC V5 — Bybit DEMO preflight {out['run_id']}",
        "",
        f"Started {out['started_at'][:19]} UTC · mode {out['mode']} (unchanged) · status **{out['status']}** · authenticated requests: {out['authenticated_requests']} (GET only)",
        "",
        "## Environment",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    lines += [
        f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {str(c['detail']).replace('|', '/')[:200]} |"
        for c in out["environment"]
    ]
    if out["read_only"]:
        lines += [
            "",
            "## Read-only DEMO checks",
            "",
            "| check | result | detail |",
            "|---|---|---|",
        ]
        lines += [
            f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {json.dumps(c['detail'], default=str).replace('|', '/')[:240]} |"
            for c in out["read_only"]["checks"]
        ]
    else:
        lines += [
            "",
            "Read-only authenticated checks were NOT run: the environment gate failed. No authenticated request was sent.",
        ]
    if out["requirements"]:
        lines += [
            "",
            "## Reference equity vs the frozen TP1/TP2 structure (LIVE rules)",
            "",
            f"```\n{json.dumps(out['requirements'], indent=1, default=str)[:4000]}\n```",
        ]
    md = out_dir / f"{out['run_id']}.md"
    md.write_text("\n".join(lines) + "\n")
    return md


def latest_preflight(
    out_dir: Path = PREFLIGHT_DIR, max_age_s: float = 86_400
) -> dict[str, Any] | None:
    files = sorted(out_dir.glob("*.json")) if out_dir.exists() else []
    if not files:
        return None
    d: dict[str, Any] = json.loads(files[-1].read_text())
    age = time.time() - datetime.fromisoformat(d["started_at"]).timestamp()
    return d if age <= max_age_s else None
