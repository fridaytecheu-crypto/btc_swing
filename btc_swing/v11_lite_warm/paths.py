"""V11-LITE-WARM artefacts: <BTC_DATA_DIR>/btc/forward_v11_lite_warm/ (observation) or
forward_v11_lite_warm_verify/ (pre-freeze verification). Never under the V10, V11 or V11-WARM roots;
no writable state is shared with any other generation."""

from __future__ import annotations

from pathlib import Path

from btc_swing.v11_warm.paths import V11WarmPaths

REPORTS_DIR = Path("reports/forward_v11_lite_warm")


class V11LiteWarmPaths(V11WarmPaths):
    def __init__(self, data_dir: Path, symbol: str, verification: bool = False) -> None:
        super().__init__(data_dir, symbol, verification)
        self.root = (
            data_dir
            / "btc"
            / ("forward_v11_lite_warm_verify" if verification else "forward_v11_lite_warm")
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
        self.control_twin = self.journals / "control_twin.jsonl"  # in-process strict V11 twin
        self.bars_1m = self.derived / "bars_1m.jsonl"
        self.diagnostic = self.derived / "diagnostic.json"
        self.seed_record = self.derived / "seed.json"
        self.state = self.root / "state.json"
        self.run_pid = self.root / "forward_run.pid"
        self.lock = self.root / "forward_run.lock"
