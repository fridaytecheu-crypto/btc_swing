"""Forward observation configuration (collection, scheduling, reporting). The STRATEGY is the frozen
V5 configuration referenced by `v5_config`; nothing here may alter a V5 rule."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from btc_swing.core.hashing import sha256_text
from btc_swing.v5.config import CollectorCfg, V5Config, load_v5_config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ForwardCollectorCfg(CollectorCfg):
    compress_closed_hours: bool = True


class SeedCfg(_Strict):
    source: str
    days_before_start: int = Field(ge=0)
    big_trade_qty_btc: float = Field(gt=0)


class BarsCfg(_Strict):
    max_gap_fill_bars: int = Field(ge=0)
    book_depth_pct: float = Field(gt=0)


class ScheduleCfg(_Strict):
    cycle_seconds: int = Field(ge=60)
    cycle_grace_seconds: int = Field(ge=0)
    daily_report_utc_minute: int = Field(ge=0, le=59)


class PaperCfg(_Strict):
    risk_per_trade: float = Field(gt=0, le=0.02)
    initial_equity: float = Field(gt=0)


class ObservationCfg(_Strict):
    minimum_days: int = Field(ge=1)
    preferred_days: int = Field(ge=1)


class ForwardConfig(_Strict):
    v5_config: str
    symbol: str
    venue: str
    collector: ForwardCollectorCfg
    seed: SeedCfg
    bars: BarsCfg
    schedule: ScheduleCfg
    paper: PaperCfg
    observation: ObservationCfg

    def canonical_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    @property
    def config_hash(self) -> str:
        return sha256_text(self.canonical_yaml())


DEFAULT_FORWARD_CONFIG_PATH = Path("config/btc_swing_v5_forward.yaml")


def load_forward_config(path: str | Path | None = None) -> ForwardConfig:
    p = Path(path) if path else DEFAULT_FORWARD_CONFIG_PATH
    with p.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return ForwardConfig.model_validate(raw)


def load_frozen_v5(fcfg: ForwardConfig, root: Path | None = None) -> V5Config:
    """The frozen strategy. Its hash must equal the one recorded in the freeze manifest."""
    p = Path(fcfg.v5_config)
    if root is not None and not p.is_absolute():
        p = root / p
    cfg = load_v5_config(p)
    if cfg.risk.risk_per_trade != fcfg.paper.risk_per_trade:
        raise ValueError("forward paper risk must equal the frozen V5 risk_per_trade")
    return cfg


class ForwardContextLike(Protocol):
    fcfg: ForwardConfig
    cfg: V5Config
    paths: ForwardPaths
    start_ms: int


class ForwardPaths:
    """Every forward artefact lives under <data_dir>/btc/forward (git-ignored) except the daily
    reports, which go to <repo>/reports/forward."""

    def __init__(self, data_dir: Path, symbol: str) -> None:
        self.root = data_dir / "btc" / "forward"
        self.raw = self.root / "bybit" / symbol
        self.seed = self.root / "seed"
        self.derived = self.root / "derived"
        self.signals = self.root / "signals"
        self.paper = self.root / "paper"
        self.logs = self.root / "logs"
        for d in (self.seed, self.derived, self.signals, self.paper, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        self.processor_state = self.derived / "processor_state.json"
        self.bars_dir = self.derived / "forward_5m"
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        self.seed_bars = self.seed / "seed_5m.parquet"
        self.seed_manifest = self.seed / "seed_manifest.jsonl"
        self.signals_file = self.signals / "signals.jsonl"
        self.outcomes_file = self.signals / "outcomes.jsonl"
        self.paper_trades = self.paper / "paper_trades.jsonl"
        self.paper_state = self.paper / "paper_state.json"
        self.cycle_log = self.logs / "cycles.jsonl"
        self.collector_pid = self.root / "collector.pid"
        self.run_pid = self.root / "forward_run.pid"
