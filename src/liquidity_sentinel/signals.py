"""Regime scoring.

Each signal contributes +1 when it points toward expanding liquidity, -1 toward
contracting, and nothing when its input is unavailable. The score is clamped to
[-5, 5], so a handful of available signals can still produce a decisive label
while a mostly-degraded run cannot.

``coverage`` is reported alongside the score: a score of +3 from nine signals and
a score of +3 from two are not the same claim, and the old tracker could not tell
them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import Snapshot

SCORE_CLAMP = 5
EXPANDING_AT = 2
CONTRACTING_AT = -2


@dataclass
class Regime:
    score: int = 0
    label: str = "neutral"
    coverage: int = 0
    available: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if self.available >= 7:
            return "high"
        if self.available >= 4:
            return "medium"
        return "low"


def _direction(value: float | None, *, expanding_when_positive: bool) -> bool | None:
    """Map a change into an expansion/contraction vote, or None if unavailable."""
    if value is None or value == 0:
        return None
    return (value > 0) if expanding_when_positive else (value < 0)


def score_regime(snapshot: Snapshot) -> Regime:
    regime = Regime()
    # (label, snapshot key, expanding when the change is positive)
    checks = (
        ("Fed net liquidity", "fed_net_liquidity_wow_bil", True),
        ("M2", "M2_wow_bil", True),
        ("stablecoins", "stablecoins_wow_usd", True),
        ("IG spreads", "BAMLC0A0CM_wow_bps", False),
        ("HY spreads", "BAMLH0A0HYM2_wow_bps", False),
        ("NFCI", "NFCI_wow", False),
        ("ANFCI", "ANFCI_wow", False),
        ("reserves", "WRESBAL_wow_bil", True),
    )

    for name, key, positive_is_expansion in checks:
        vote = _direction(snapshot.get(key), expanding_when_positive=positive_is_expansion)
        if vote is None:
            continue
        regime.available += 1
        regime.score += 1 if vote else -1
        regime.notes.append(("+" if vote else "-") + name)

    # Funding: prefer the SOFR-IORB spread, fall back to the SOFR level change.
    funding = snapshot.get("SOFR_minus_IORB_wow_bps")
    if funding is None:
        funding = snapshot.get("SOFR_wow_bps")
    if funding is not None:
        regime.available += 1
        expanding = funding <= 0
        regime.score += 1 if expanding else -1
        regime.notes.append(("+" if expanding else "-") + "funding")

    regime.coverage = regime.available
    regime.score = max(-SCORE_CLAMP, min(SCORE_CLAMP, regime.score))
    if regime.score >= EXPANDING_AT:
        regime.label = "expanding"
    elif regime.score <= CONTRACTING_AT:
        regime.label = "contracting"
    else:
        regime.label = "neutral"
    return regime
