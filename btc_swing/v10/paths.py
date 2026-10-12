"""Every V10 artefact lives under <BTC_DATA_DIR>/btc/forward_v10/ (observation) or
<BTC_DATA_DIR>/btc/forward_v10_verify/ (pre-freeze shadow verification). Nothing under the V5 / V5.1
forward roots is ever written by V10."""

from __future__ import annotations

from pathlib import Path

REPORTS_DIR = Path("reports/forward_v10")


class V10Paths:
    def __init__(self, data_dir: Path, symbol: str, verification: bool = False) -> None:
        self.data_dir = data_dir
        self.verification = verification
        self.root = data_dir / "btc" / ("forward_v10_verify" if verification else "forward_v10")
        self.raw = self.root / "raw" / symbol
        self.journals = self.root / "journals"
        self.derived = self.root / "derived"
        self.logs = self.root / "logs"
        self.host = self.root / "host"
        self.demo = self.root / "demo"
        self.episodes = self.journals / "episodes.jsonl"
        self.signals = self.journals / "signals.jsonl"
        self.nearmiss = self.journals / "nearmiss.jsonl"
        self.shadow_trades = self.journals / "shadow_trades.jsonl"
        self.outcomes = self.journals / "outcomes.jsonl"
        self.bars_1m = self.derived / "bars_1m.jsonl"
        self.state = self.root / "state.json"
        self.run_pid = self.root / "forward_run.pid"
        self.lock = self.root / "forward_run.lock"
        self.demo_journal = self.demo / "strategy_journal.jsonl"
        self.demo_state = self.demo / "strategy_state.json"
        self.demo_trades = self.demo / "demo_trades.jsonl"
        # V5 / V5.1 roots are READ ONLY for V10 (conflict checks)
        self.v5_root = data_dir / "btc" / "forward"
        self.v51_root = data_dir / "btc" / "forward_v51"

    def ensure(self) -> None:
        for d in (self.raw, self.journals, self.derived, self.logs, self.host, self.demo):
            d.mkdir(parents=True, exist_ok=True)
