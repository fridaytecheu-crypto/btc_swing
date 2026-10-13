"""V11 forward artefacts: <BTC_DATA_DIR>/btc/forward_v11/ (observation) or forward_v11_verify/
(pre-freeze verification). V11 never writes under another generation's forward root."""

from __future__ import annotations

from pathlib import Path

REPORTS_DIR = Path("reports/forward_v11")


class V11Paths:
    def __init__(self, data_dir: Path, symbol: str, verification: bool = False) -> None:
        self.data_dir = data_dir
        self.verification = verification
        self.root = data_dir / "btc" / ("forward_v11_verify" if verification else "forward_v11")
        self.raw = self.root / "raw" / symbol
        self.journals = self.root / "journals"
        self.derived = self.root / "derived"
        self.logs = self.root / "logs"
        self.candidates = self.journals / "candidates.jsonl"
        self.episodes = self.journals / "episodes.jsonl"
        self.signals = self.journals / "signals.jsonl"
        self.nearmiss = self.journals / "nearmiss.jsonl"
        self.shadow_trades = self.journals / "shadow_trades.jsonl"
        self.outcomes = self.journals / "outcomes.jsonl"
        self.bars_1m = self.derived / "bars_1m.jsonl"
        self.diagnostic = self.derived / "diagnostic.json"
        self.state = self.root / "state.json"
        self.run_pid = self.root / "forward_run.pid"
        self.lock = self.root / "forward_run.lock"

    def ensure(self) -> None:
        for d in (self.raw, self.journals, self.derived, self.logs):
            d.mkdir(parents=True, exist_ok=True)
