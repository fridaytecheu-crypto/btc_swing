from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from btc_swing.v5.demo.client import BybitDemoClient, sign
from btc_swing.v5.demo.guard import (
    DemoCredentials,
    DemoGuardError,
    ExecutionDisabledError,
    ExecutionMode,
    MissingDemoCredentialsError,
    assert_demo_private_ws_url,
    assert_demo_rest_url,
    mode_from_env,
)
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.demo.readonly import render_readonly_report, run_readonly_verification

KEY, SECRET = "TESTKEY123456", "TESTSECRETabcdef0123456789"
ENV = {"BYBIT_DEMO_API_KEY": KEY, "BYBIT_DEMO_API_SECRET": SECRET}


class FakeDemo:
    """Simulated api-demo.bybit.com: checks host, headers and the HMAC signature of every call."""

    def __init__(self, status: int = 200, raw: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status, self.raw = status, raw

    def __call__(self, method: str, url: str, headers: dict[str, str], body: str | None):
        u = urlsplit(url)
        assert u.hostname == "api-demo.bybit.com" and u.scheme == "https"
        self.calls.append((method, u.path))
        if self.raw is not None:
            return self.status, self.raw
        payload = u.query if method == "GET" else (body or "")
        exp = hmac.new(
            SECRET.encode(),
            f"{headers['X-BAPI-TIMESTAMP']}{KEY}{headers['X-BAPI-RECV-WINDOW']}{payload}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if headers.get("X-BAPI-API-KEY") != KEY or headers.get("X-BAPI-SIGN") != exp:
            return 200, {"retCode": 10004, "retMsg": "error sign!", "result": {}}
        q = dict(parse_qsl(u.query))
        if u.path == "/v5/user/query-api":
            return 200, {
                "retCode": 0,
                "retMsg": "",
                "result": {
                    "apiKey": KEY,
                    "readOnly": 0,
                    "permissions": {"ContractTrade": ["Order", "Position"]},
                    "isMaster": True,
                },
            }
        if u.path == "/v5/account/wallet-balance":
            assert q == {"accountType": "UNIFIED"}
            return 200, {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "50000",
                            "coin": [{"coin": "USDT", "equity": "50000", "walletBalance": "50000"}],
                        }
                    ]
                },
            }
        if u.path == "/v5/position/list":
            return 200, {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "",
                            "size": "0",
                            "avgPrice": "0",
                            "positionIdx": 0,
                        }
                    ]
                },
            }
        if u.path == "/v5/order/realtime":
            return 200, {"retCode": 0, "retMsg": "OK", "result": {"list": []}}
        return 404, {"retCode": -1, "retMsg": "unknown path"}


def test_guard_allows_only_the_demo_host() -> None:
    assert (
        assert_demo_rest_url("https://api-demo.bybit.com/v5/order/create") == "api-demo.bybit.com"
    )
    for bad in (
        "https://api.bybit.com/v5/order/create",
        "https://api.bytick.com/v5/order/create",
        "https://api-testnet.bybit.com/v5/order/create",
        "http://api-demo.bybit.com/v5/order/create",
        "https://api-demo.bybit.com.evil.example/v5",
        "https://api-demo.bybit.com:8443/v5",
    ):
        with pytest.raises(DemoGuardError):
            assert_demo_rest_url(bad)
    assert (
        assert_demo_private_ws_url("wss://stream-demo.bybit.com/v5/private")
        == "stream-demo.bybit.com"
    )
    with pytest.raises(DemoGuardError):
        assert_demo_private_ws_url("wss://stream.bybit.com/v5/private")


def test_mode_defaults_to_disabled_and_rejects_unknown() -> None:
    assert mode_from_env({}) is ExecutionMode.DISABLED
    assert (
        mode_from_env({"BYBIT_EXECUTION_MODE": "EXECUTION_SMOKE"}) is ExecutionMode.EXECUTION_SMOKE
    )
    with pytest.raises(DemoGuardError):
        mode_from_env({"BYBIT_EXECUTION_MODE": "LIVE"})


def test_credentials_fail_closed_and_never_render() -> None:
    with pytest.raises(MissingDemoCredentialsError):
        DemoCredentials.from_env({"BYBIT_DEMO_API_KEY": KEY})
    c = DemoCredentials.from_env(ENV)
    assert SECRET not in repr(c) and KEY not in str(c)
    assert c.redact({"a": f"x{SECRET}y", "b": [KEY]}) == {"a": "x<redacted>y", "b": ["<redacted>"]}


def test_signature_matches_bybit_v5_definition() -> None:
    exp = hmac.new(b"s", b"1700000000000k5000category=linear", hashlib.sha256).hexdigest()
    assert sign("s", "1700000000000", "k", "5000", "category=linear") == exp


def test_disabled_mode_refuses_state_changing_requests_without_network(tmp_path: Path) -> None:
    fake = FakeDemo()
    cl = BybitDemoClient(
        DemoCredentials.from_env(ENV),
        ExecutionMode.DISABLED,
        HashChainJournal(tmp_path / "j.jsonl"),
        fake,
        "T",
    )
    with pytest.raises(ExecutionDisabledError):
        cl.post(
            "/v5/order/create",
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "orderType": "Market",
                "qty": "0.001",
            },
        )
    assert fake.calls == []
    assert cl.get("/v5/order/realtime", {"category": "linear", "symbol": "BTCUSDT"}).ok
    assert fake.calls == [("GET", "/v5/order/realtime")]


def test_readonly_verification_passes_on_the_demo_host_and_journals_no_secret(
    tmp_path: Path,
) -> None:
    fake = FakeDemo()
    jp = tmp_path / "ro.jsonl"
    r = run_readonly_verification(jp, fake, ENV)
    assert all(c.passed for c in r.checks), [c.detail for c in r.checks if not c.passed]
    assert r.production_auth_endpoint_used is False and r.hosts_contacted == ["api-demo.bybit.com"]
    assert {m for m, _ in fake.calls} == {"GET"}
    text = jp.read_text()
    assert SECRET not in text and KEY not in text and "X-BAPI-SIGN" not in text
    ok, n, _ = HashChainJournal.verify(jp)
    assert ok and n >= 10
    md = render_readonly_report(r)
    assert "DEMO AUTH | PASSED" in md and "PRODUCTION AUTH ENDPOINT USED | NO" in md


def test_readonly_verification_fails_closed_without_credentials(tmp_path: Path) -> None:
    fake = FakeDemo()
    r = run_readonly_verification(tmp_path / "ro.jsonl", fake, {})
    assert not any(c.passed for c in r.checks) and fake.calls == []
    assert r.production_auth_endpoint_used is False and r.blocker and "credentials" in r.blocker


def test_readonly_verification_reports_a_geo_block(tmp_path: Path) -> None:
    fake = FakeDemo(
        403,
        {
            "raw_text": "The Amazon CloudFront distribution is configured to block access from your country."
        },
    )
    r = run_readonly_verification(tmp_path / "ro.jsonl", fake, ENV)
    assert r.line("DEMO AUTH") == "FAILED" and r.line("WALLET QUERY") == "FAILED"
    assert r.blocker and "403" in r.blocker and "CloudFront" in r.blocker


def test_journal_detects_tampering(tmp_path: Path) -> None:
    jp = tmp_path / "j.jsonl"
    j = HashChainJournal(jp)
    for i in range(3):
        j.append({"i": i})
    assert HashChainJournal.verify(jp)[0]
    lines = jp.read_text().splitlines()
    lines[1] = json.dumps({**json.loads(lines[1]), "i": 99}, sort_keys=True, separators=(",", ":"))
    jp.write_text("\n".join(lines) + "\n")
    assert not HashChainJournal.verify(jp)[0]
