"""V11-WARM artefacts: <BTC_DATA_DIR>/btc/forward_v11_warm/ (observation) or forward_v11_warm_verify/
(pre-freeze verification). Never under the V10 or V11 roots."""

from __future__ import annotations

from pathlib import Path

from btc_swing.v11.paths import V11Paths

REPORTS_DIR = Path("reports/forward_v11_warm")


class V11WarmPaths(V11Paths):
    def __init__(self, data_dir: Path, symbol: str, verification: bool = False) -> None:
        super().__init__(data_dir, symbol, verification)
        self.root = (
            data_dir / "btc" / ("forward_v11_warm_verify" if verification else "forward_v11_warm")
        )
        self.raw = self.root / "raw" / symbol
        self.journals = self.root / "journals"
        self.derived = self.root / "derived"
        self.logs = self.root / "logs"
        self.bootstrap = self.root / "bootstrap"
        self.candidates = self.journals / "candidates.jsonl"
        self.episodes = self.journals / "episodes.jsonl"
        self.signals = self.journals / "signals.jsonl"
        self.nearmiss = self.journals / "nearmiss.jsonl"
        self.shadow_trades = self.journals / "shadow_trades.jsonl"
        self.outcomes = self.journals / "outcomes.jsonl"
        self.bars_1m = self.derived / "bars_1m.jsonl"
        self.diagnostic = self.derived / "diagnostic.json"
        self.seed_record = self.derived / "seed.json"
        self.state = self.root / "state.json"
        self.run_pid = self.root / "forward_run.pid"
        self.lock = self.root / "forward_run.lock"

    def ensure(self) -> None:
        for d in (self.raw, self.journals, self.derived, self.logs, self.bootstrap):
            d.mkdir(parents=True, exist_ok=True)
