"""V11-LITE engine: the frozen V11 engine with ONE change. `structure()` no longer gates arming; the
A/B/C structure conditions are still computed by the unchanged `V11Engine.structure` and exposed as
labels. Triggers, stop geometry, exits, sizing and costs are inherited untouched. Signal ids carry the
`V11LWS-` prefix so no record can be mistaken for a V11 / V11-WARM one."""

from __future__ import annotations

from typing import Any

import numpy as np

from btc_swing.v11.engine import FAMILIES, V11Engine
from btc_swing.v11_lite_warm.config import PREFIX_SIGNAL


class V11LiteEngine(V11Engine):
    def structure(self, fam: str, s: int, i: int) -> bool:
        """THE strategy delta. Data validity only (close and ATR5 finite, exactly the guard the V11
        structure check starts with); the family structure itself never vetoes arming."""
        c, a5 = self.X["close"][i], self.X["atr5"][i]
        return bool(np.isfinite(c) and np.isfinite(a5) and a5 > 0)

    def family_structure(self, fam: str, s: int, i: int) -> bool:
        """The frozen V11 structure condition, as a label (read-only; never consulted by arming)."""
        return V11Engine.structure(self, fam, s, i)

    def family_labels(self, i: int) -> dict[str, dict[str, bool]]:
        return {
            fam: {
                "LONG": self.family_structure(fam, 1, i),
                "SHORT": self.family_structure(fam, -1, i),
            }
            for fam in FAMILIES
        }

    def _decide(self, fam: str, s: int, i: int, arm: dict[str, Any]) -> dict[str, Any]:
        rec = super()._decide(fam, s, i, arm)
        rec["signal_id"] = PREFIX_SIGNAL + str(rec["signal_id"]).removeprefix("V11S-")
        rec["family_labels"] = self.family_labels(i)
        rec["family_structure_label"] = bool(rec["family_labels"][fam][rec["side"]])
        return rec
