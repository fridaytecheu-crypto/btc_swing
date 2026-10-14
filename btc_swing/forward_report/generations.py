"""Spec builders: how each generation's artefacts map onto the generic reporting layer. Only paths and
config hashes are read; no engine, runner, executor or exchange module is imported."""

from __future__ import annotations

from pathlib import Path

from btc_swing.core.settings import get_settings
from btc_swing.forward_report.spec import GenerationSpec


def spec_v10(config: Path, verification: bool) -> tuple[GenerationSpec, float | None]:
    from btc_swing.v10.config import load_v10_config
    from btc_swing.v10.freeze import FREEZE_PATH_V10
    from btc_swing.v10.paths import REPORTS_DIR, V10Paths

    cfg = load_v10_config(config)
    p = V10Paths(get_settings().data_dir, cfg.symbol, verification=verification)
    spec = GenerationSpec(
        name="v10",
        label="V10 LIVE MICROSTRUCTURE SNIPER",
        root=p.root,
        reports_dir=REPORTS_DIR,
        state=p.state,
        freeze=FREEZE_PATH_V10,
        strategy_hash=cfg.strategy_hash,
        journals={
            "episodes": p.episodes,
            "signals": p.signals,
            "nearmiss": p.nearmiss,
            "shadow_trades": p.shadow_trades,
            "outcomes": p.outcomes,
        },
        demo_journal=p.demo_journal,
        demo_trades=p.demo_trades,
        demo_state=p.demo_state,
        execution_modes=("SHADOW", "DEMO"),
        verification=verification,
        notes={
            "scores": "V10 has no multi-factor score; candidates and score distributions are N/A",
            "triggers": "journaled as EPISODE_TRIGGERED",
        },
    )
    return spec, float(cfg.risk.initial_equity)


def spec_v11(config: Path, verification: bool) -> tuple[GenerationSpec, float | None]:
    from btc_swing.v11.config import load_v11_config
    from btc_swing.v11.freeze import FREEZE_PATH_V11
    from btc_swing.v11.paths import REPORTS_DIR, V11Paths

    cfg = load_v11_config(config)
    p = V11Paths(get_settings().data_dir, cfg.symbol, verification=verification)
    spec = GenerationSpec(
        name="v11",
        label="V11 FAST MULTI-FACTOR INTRADAY (shadow)",
        root=p.root,
        reports_dir=REPORTS_DIR,
        state=p.state,
        freeze=FREEZE_PATH_V11,
        strategy_hash=cfg.strategy_hash,
        journals={
            "candidates": p.candidates,
            "episodes": p.episodes,
            "signals": p.signals,
            "nearmiss": p.nearmiss,
            "shadow_trades": p.shadow_trades,
            "outcomes": p.outcomes,
        },
        execution_modes=("SHADOW",),
        verification=verification,
        notes={
            "triggers": "not journaled per event; the state counter is reported",
            "invalidated/expired": "N/A (armed states expire silently after their validity window)",
        },
    )
    return spec, float(cfg.risk.initial_equity)


def spec_v11w(config: Path, verification: bool) -> tuple[GenerationSpec, float | None]:
    from btc_swing.v11_warm.config import load_v11w_config
    from btc_swing.v11_warm.freeze import FREEZE_PATH_V11W
    from btc_swing.v11_warm.paths import REPORTS_DIR, V11WarmPaths

    _wc, v11 = load_v11w_config(config)
    p = V11WarmPaths(get_settings().data_dir, v11.symbol, verification=verification)
    spec = GenerationSpec(
        name="v11w",
        label="V11-WARM WARM-STARTED V11 SHADOW",
        root=p.root,
        reports_dir=REPORTS_DIR,
        state=p.state,
        freeze=FREEZE_PATH_V11W,
        strategy_hash=v11.strategy_hash,
        journals={
            "candidates": p.candidates,
            "episodes": p.episodes,
            "signals": p.signals,
            "nearmiss": p.nearmiss,
            "shadow_trades": p.shadow_trades,
            "outcomes": p.outcomes,
        },
        execution_modes=("SHADOW",),
        verification=verification,
        notes={
            "triggers": "not journaled per event; the state counter is reported",
            "warm_start": "bars before observation_start are bootstrap / kline context only",
        },
    )
    return spec, float(v11.risk.initial_equity)
