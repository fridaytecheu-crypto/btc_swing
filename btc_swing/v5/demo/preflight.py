"""Preflight for real Bybit DEMO validation, in two separated gates. Mode stays DISABLED.

DEMO_EXECUTION_PREFLIGHT (required for EXECUTION_SMOKE): not an ephemeral cloud-session container,
configured mode and BYBIT_EXECUTION_MODE (if set) DISABLED, demo-only endpoint allowlist, demo
credentials present, Bybit DEMO REST reachable (unauthenticated probe); then, only if those pass,
signed READ-ONLY checks with a GET-only client: authentication, API key information, DEMO account
confirmation, UNIFIED wallet, BTCUSDT position and open orders (flat, none open), live instrument
rules, private DEMO WebSocket authentication, and proof that no production endpoint was contacted.

FORWARD_HOST_PREFLIGHT (required, together with a PASSED smoke, before STRATEGY_DEMO; never for
EXECUTION_SMOKE): persistent host, forward runner running on this host, exactly one forward runner,
systemd service active, forward freeze V5 hash and observation start unchanged. These checks are
local (no network) and are re-evaluated live whenever a STRATEGY_DEMO executor is built.
"""

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
from btc_swing.v5.demo.config import (
    DEMO_REST_HOST,
    DEMO_WS_PRIVATE_HOST,
    DemoExecConfig,
    EndpointNotAllowedError,
    ExecutionMode,
    assert_demo_url,
    assert_demo_ws_url,
)
from btc_swing.v5.demo.credentials import (
    DemoCredentials,
    DemoCredentialsMissingError,
    credentials_present,
    load_demo_credentials,
)
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.demo.requirements import reference_equity_report
from btc_swing.v5.demo.ws import private_ws_auth

EXPECTED_V5_HASH = "d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177"
EXPECTED_OBSERVATION_START_MS = 1791385162972  # 2026-10-07T14:59:22.972Z
PREFLIGHT_DIR = Path("reports/forward/demo_preflight")
PREFLIGHT_TAG = "PREFLIGHT_READ_ONLY"
DEMO_GATE = "DEMO_EXECUTION_PREFLIGHT"
HOST_GATE = "FORWARD_HOST_PREFLIGHT"
SERVICE = "btc-v5-forward"


def is_cloud_session_container() -> bool:
    """True inside an ephemeral Claude Code cloud-session container (not a persistent host)."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true" or Path("/root/.ccr").exists()


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
            try:
                cmd = subprocess.run(
                    ["ps", "-o", "command=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
            except FileNotFoundError:
                continue
        if "python" in cmd and "btc-swing v5 forward run" in cmd:
            pids.append(pid)
    return pids


def _systemd_service_active() -> tuple[bool, str]:
    if not Path("/run/systemd/system").exists():
        return False, "systemd not running on this machine"
    try:
        r = subprocess.run(
            ["systemctl", "is-active", SERVICE], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return False, "systemctl not found"
    state = r.stdout.strip() or r.stderr.strip()
    return state == "active", f"{SERVICE}: {state}"


def _check(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def demo_environment_checks(
    dcfg: DemoExecConfig, probe: Callable[[], tuple[int, str]] | None = None
) -> list[dict[str, Any]]:
    """Local and unauthenticated checks of the DEMO_EXECUTION gate (no credential is sent)."""
    checks: list[dict[str, Any]] = []
    cloud = is_cloud_session_container()
    checks.append(
        _check(
            "not an ephemeral cloud-session container (no order from the cloud environment)",
            not cloud,
            "cloud-session container detected" if cloud else "ok",
        )
    )
    env_mode = os.environ.get("BYBIT_EXECUTION_MODE", "")
    checks.append(
        _check(
            "execution mode DISABLED during preflight (config and BYBIT_EXECUTION_MODE)",
            dcfg.mode is ExecutionMode.DISABLED and env_mode in ("", ExecutionMode.DISABLED.value),
            f"config {dcfg.mode.value}; BYBIT_EXECUTION_MODE={env_mode or '(unset)'}",
        )
    )
    try:
        assert_demo_url(dcfg.rest_base)
        assert_demo_ws_url(dcfg.ws_private)
        allow_ok, allow_detail = True, f"{dcfg.rest_base} / {dcfg.ws_private}"
    except EndpointNotAllowedError as e:
        allow_ok, allow_detail = False, str(e)
    checks.append(_check("demo-only endpoint allowlist", allow_ok, allow_detail))
    checks.append(
        _check(
            "demo credentials present (BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET)",
            credentials_present(dcfg),
            "present" if credentials_present(dcfg) else "missing (values are never printed)",
        )
    )
    try:
        status, body = probe() if probe else _probe()
        geo = "country" in body.lower()
        checks.append(
            _check(
                f"Bybit DEMO REST reachable ({DEMO_REST_HOST}, unauthenticated probe)",
                status == 200,
                f"HTTP {status}" + (" (Bybit country restriction)" if geo else ""),
            )
        )
    except httpx.HTTPError as e:
        checks.append(
            _check(
                f"Bybit DEMO REST reachable ({DEMO_REST_HOST}, unauthenticated probe)",
                False,
                f"{type(e).__name__}: {e}"[:200],
            )
        )
    return checks


def forward_host_checks(
    freeze: dict[str, Any] | None, v5_hash: str, pid_file: Path
) -> list[dict[str, Any]]:
    """FORWARD_HOST gate: local only. Mandatory before STRATEGY_DEMO, never for EXECUTION_SMOKE."""
    checks: list[dict[str, Any]] = []
    cloud = is_cloud_session_container()
    checks.append(
        _check(
            "persistent host (not an ephemeral cloud-session container)",
            not cloud,
            "cloud-session container detected" if cloud else "ok",
        )
    )
    alive = False
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
            alive = True
        except (OSError, ValueError):
            alive = False
    pids = _runner_pids()
    checks.append(
        _check(
            "forward runner running on this host",
            alive and len(pids) >= 1,
            f"pid file alive={alive}; runner processes={pids}",
        )
    )
    checks.append(
        _check("exactly one forward runner", len(pids) == 1, f"{len(pids)} runner process(es)")
    )
    sd_ok, sd_detail = _systemd_service_active()
    checks.append(_check("systemd deployment active", sd_ok, sd_detail))
    fz: dict[str, Any] = freeze or {}
    checks.append(
        _check(
            "forward freeze present and V5 hash unchanged",
            fz.get("v5_config_hash") == EXPECTED_V5_HASH == v5_hash,
            f"freeze {str(fz.get('v5_config_hash', 'missing'))[:12]} / config {v5_hash[:12]} / expected {EXPECTED_V5_HASH[:12]}",
        )
    )
    checks.append(
        _check(
            "observation start unchanged",
            int(fz.get("observation_start_ms", -1)) == EXPECTED_OBSERVATION_START_MS,
            str(fz.get("observation_start", "missing")),
        )
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
    recs = [
        r
        for r in journal.records()
        if r.get("tag") == PREFLIGHT_TAG and "request" in r.get("data", {})
    ]
    req_hosts = sorted({str(r["data"]["request"].get("host")) for r in recs})
    add(
        "production authenticated endpoint never used",
        req_hosts in ([], [DEMO_REST_HOST])
        and str(cl._http.base_url.host) == DEMO_REST_HOST
        and str(ws.get("endpoint", "")).startswith(f"wss://{DEMO_WS_PRIVATE_HOST}/"),
        {
            "rest_hosts_contacted": req_hosts,
            "ws_endpoint": ws.get("endpoint"),
            "requests": len(recs),
        },
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
    env = demo_environment_checks(dcfg, kw.pop("probe", None))
    out: dict[str, Any] = {
        "run_id": started.strftime("%y%m%d%H%M%S"),
        "started_at": started.isoformat(),
        "mode": dcfg.mode.value,
        "read_only": None,
        "requirements": None,
    }
    demo_checks = list(env)
    if all(c["ok"] for c in env):
        ro = read_only_checks(dcfg, journal, **kw)
        out["read_only"] = ro
        demo_checks += ro["checks"]
        inst = ro["data"].get("BTCUSDT instrument rules")
        px = (ro["data"].get("ticker") or {}).get("last") or price_fallback
        if inst and px:
            out["requirements"] = {
                "source": "LIVE instrument rules",
                **reference_equity_report(
                    dcfg.reference_equity_usdt, dcfg.risk_per_trade, float(px), inst
                ),
            }
    demo_ok = out["read_only"] is not None and all(c["ok"] for c in demo_checks)
    host = forward_host_checks(freeze, cfg_hash, pid_file)
    out["gates"] = {
        DEMO_GATE: {
            "status": "PASSED"
            if demo_ok
            else ("BLOCKED_ENVIRONMENT" if out["read_only"] is None else "FAILED"),
            "checks": demo_checks,
            "required_for": ["EXECUTION_SMOKE", "STRATEGY_DEMO"],
        },
        HOST_GATE: {
            "status": "PASSED" if all(c["ok"] for c in host) else "FAILED",
            "checks": host,
            "required_for": ["STRATEGY_DEMO"],
        },
    }
    out["status"] = out["gates"][DEMO_GATE]["status"]  # the gate EXECUTION_SMOKE needs
    out["finished_at"] = datetime.now(UTC).isoformat()
    out["authenticated_requests"] = sum(
        1
        for r in journal.records()
        if r.get("tag") == PREFLIGHT_TAG and r["data"].get("request", {}).get("auth")
    )
    return out


def smoke_allowed(pf: dict[str, Any] | None) -> tuple[bool, str]:
    """EXECUTION_SMOKE needs only a PASSED DEMO_EXECUTION_PREFLIGHT (forward-host gate not required)."""
    if not pf:
        return False, "no preflight report in the last 24 h"
    st = ((pf.get("gates") or {}).get(DEMO_GATE) or {}).get("status")
    return (st == "PASSED"), f"{DEMO_GATE} {st or 'missing (older preflight format)'}"


def require_forward_host(
    freeze: dict[str, Any] | None, v5_hash: str, pid_file: Path
) -> list[dict[str, Any]]:
    """Fail closed for STRATEGY_DEMO unless every FORWARD_HOST check passes right now."""
    checks = forward_host_checks(freeze, v5_hash, pid_file)
    failed = [c["check"] for c in checks if not c["ok"]]
    if failed:
        raise RuntimeError(f"{HOST_GATE} failed: {', '.join(failed)}")
    return checks


def _gate_table(title: str, gate: dict[str, Any]) -> list[str]:
    lines = [
        "",
        f"## {title}: {gate['status']} (required for {', '.join(gate['required_for'])})",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    lines += [
        f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {json.dumps(c['detail'], default=str).replace('|', '/')[:240]} |"
        for c in gate["checks"]
    ]
    return lines


def write_preflight(out: dict[str, Any], out_dir: Path = PREFLIGHT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{out['run_id']}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True, default=str)
    )
    g = out["gates"]
    lines = [
        f"# BTC V5 — Bybit DEMO preflight {out['run_id']}",
        "",
        f"Started {out['started_at'][:19]} UTC · mode {out['mode']} (unchanged) · authenticated requests: {out['authenticated_requests']} (GET only)",
        "",
        f"- {DEMO_GATE}: **{g[DEMO_GATE]['status']}** -> EXECUTION_SMOKE {'allowed' if g[DEMO_GATE]['status'] == 'PASSED' else 'NOT allowed'}",
        f"- {HOST_GATE}: **{g[HOST_GATE]['status']}** -> STRATEGY_DEMO {'possible after a PASSED smoke' if g[HOST_GATE]['status'] == 'PASSED' and g[DEMO_GATE]['status'] == 'PASSED' else 'NOT allowed (fails closed)'}",
    ]
    lines += _gate_table(DEMO_GATE, g[DEMO_GATE])
    if out["read_only"] is None:
        lines += [
            "",
            "Signed read-only checks were NOT run: a local/unauthenticated demo check failed. No authenticated request was sent.",
        ]
    lines += _gate_table(HOST_GATE, g[HOST_GATE])
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
