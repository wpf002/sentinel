"""Threshold rules.

An alert carries the value that tripped it and the threshold it crossed, so the
notification says *how far* past the line the reading is rather than only that a
line was crossed.

Rules are evaluated only against series the run considers fresh. The original
tracker re-evaluated the whole FRED rule set against a snapshot that had not
moved in six weeks, which is how a stale reading could keep re-firing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import Snapshot

# Series whose staleness suppresses the rules that depend on them.
RULE_INPUTS = {
    "fed_net_liquidity_wow_bil": ("WALCL", "WTREGEN", "RRPONTSYD"),
    "WALCL_wow_bil": ("WALCL",),
    "WRESBAL_wow_bil": ("WRESBAL",),
    "WTREGEN_wow_bil": ("WTREGEN",),
    "RRPONTSYD_wow_bil": ("RRPONTSYD",),
    "WRESBAL_bil": ("WRESBAL",),
    "M2_wow_pct": ("WM2NS",),
    "BAMLC0A0CM_wow_bps": ("BAMLC0A0CM",),
    "BAMLH0A0HYM2_wow_bps": ("BAMLH0A0HYM2",),
    "NFCI_wow": ("NFCI",),
    "ANFCI_wow": ("ANFCI",),
    "DGS2_wow_bps": ("DGS2",),
    "DGS10_wow_bps": ("DGS10",),
    "T10Y2Y_wow_bps": ("T10Y2Y",),
    "SOFR_wow_bps": ("SOFR",),
    "EFFR_wow_bps": ("EFFR",),
    "IORB_wow_bps": ("IORB",),
    "SOFR_minus_IORB_wow_bps": ("SOFR", "IORB"),
    "SOFR_minus_EFFR_wow_bps": ("SOFR", "EFFR"),
}


@dataclass(frozen=True)
class Alert:
    key: str
    message: str
    value: float
    threshold: float
    severity: str = "info"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "severity": self.severity,
        }


def _fmt_bil(value: float) -> str:
    return f"${value:+,.1f}B"


def _fmt_usd(value: float) -> str:
    return f"${value / 1e9:+,.2f}B"


def _fmt_bps(value: float) -> str:
    return f"{value:+,.0f} bps"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+,.2f}%"


class AlertEvaluator:
    """Evaluates the configured thresholds against one snapshot."""

    def __init__(self, snapshot: Snapshot, thresholds: dict[str, float]):
        self.snapshot = snapshot
        self.thresholds = thresholds
        self.stale = set(snapshot.stale_series)
        self.alerts: list[Alert] = []

    def _usable(self, key: str) -> float | None:
        """Return the value only when every series it depends on is fresh."""
        needed = RULE_INPUTS.get(key, ())
        if any(series in self.stale for series in needed):
            return None
        return self.snapshot.get(key)

    def _abs_rule(self, key: str, threshold_name: str, label: str, fmt, severity="warning") -> None:
        threshold = self.thresholds.get(threshold_name)
        value = self._usable(key)
        if threshold is None or value is None or abs(value) < threshold:
            return
        self.alerts.append(
            Alert(
                key,
                f"{label} {fmt(value)} (threshold {fmt(threshold)})",
                value,
                threshold,
                severity,
            )
        )

    def _reserves_floor_crossing(self) -> None:
        """Alert when reserves cross the floor, in either direction.

        This was a level rule: ``if reserves_level < floor``. That condition is
        true on every run for as long as reserves stay below, so it re-fired for
        the six weeks reserves sat under $3,000B in 2026 -- six notifications for
        one standing fact, spaced only by the dedupe window.

        The prior-week level is the current level minus the week-over-week
        change, both already in the snapshot, so the crossing is detected without
        reading the previous run and without depending on run cadence. The
        standing condition is still reported; ``report`` flags it on the reserves
        line. It just is not an alert.
        """
        floor = self.thresholds.get("reserves_floor_bil")
        level = self._usable("WRESBAL_bil")
        change = self._usable("WRESBAL_wow_bil")
        if floor is None or level is None or change is None:
            return
        prior = level - change

        if prior >= floor > level:
            self.alerts.append(
                Alert(
                    "reserves_floor_breach",
                    f"Bank reserves fell through the ${floor:,.0f}B floor "
                    f"to ${level:,.0f}B (from ${prior:,.0f}B)",
                    level,
                    floor,
                    "critical",
                )
            )
        elif level >= floor > prior:
            self.alerts.append(
                Alert(
                    "reserves_floor_recovery",
                    f"Bank reserves recovered above the ${floor:,.0f}B floor "
                    f"to ${level:,.0f}B (from ${prior:,.0f}B)",
                    level,
                    floor,
                    "warning",
                )
            )

    def evaluate(self) -> list[Alert]:
        t = self.thresholds
        s = self.snapshot

        # Stablecoins: the always-on signal, never gated on FRED.
        pct = s.get("stablecoins_wow_pct")
        if pct is not None and abs(pct) >= t["stablecoins_wow_pct"]:
            self.alerts.append(
                Alert(
                    "stablecoins_wow_pct",
                    f"Stablecoin supply {_fmt_pct(pct)} WoW "
                    f"(threshold {_fmt_pct(t['stablecoins_wow_pct'])})",
                    pct,
                    t["stablecoins_wow_pct"],
                    "warning",
                )
            )
        usd = s.get("stablecoins_wow_usd")
        if usd is not None and abs(usd) >= t["stablecoins_wow_usd"]:
            self.alerts.append(
                Alert(
                    "stablecoins_wow_usd",
                    f"Stablecoin supply {_fmt_usd(usd)} WoW "
                    f"(threshold {_fmt_usd(t['stablecoins_wow_usd'])})",
                    usd,
                    t["stablecoins_wow_usd"],
                    "warning",
                )
            )

        # Fed balance sheet.
        self._abs_rule("fed_net_liquidity_wow_bil", "fed_net_liquidity_wow_bil",
                       "Fed net liquidity", _fmt_bil, "critical")
        for series in ("WALCL", "WRESBAL", "WTREGEN", "RRPONTSYD"):
            self._abs_rule(f"{series}_wow_bil", "fed_component_wow_bil", f"{series}", _fmt_bil)

        reserves_change = self._usable("WRESBAL_wow_bil")
        if reserves_change is not None and reserves_change <= -t["reserves_drop_bil"]:
            self.alerts.append(
                Alert("reserves_drop", f"Bank reserves {_fmt_bil(reserves_change)} WoW",
                      reserves_change, -t["reserves_drop_bil"], "critical")
            )
        self._reserves_floor_crossing()

        self._abs_rule("M2_wow_pct", "m2_wow_pct", "M2", _fmt_pct)
        self._abs_rule("BAMLC0A0CM_wow_bps", "ig_spread_wow_bps", "IG spread", _fmt_bps)
        self._abs_rule("BAMLH0A0HYM2_wow_bps", "hy_spread_wow_bps", "HY spread", _fmt_bps)
        self._abs_rule("NFCI_wow", "nfci_wow", "NFCI", lambda v: f"{v:+.3f}")
        self._abs_rule("ANFCI_wow", "anfci_wow", "ANFCI", lambda v: f"{v:+.3f}")
        self._abs_rule("DGS2_wow_bps", "yield_wow_bps", "2Y yield", _fmt_bps)
        self._abs_rule("DGS10_wow_bps", "yield_wow_bps", "10Y yield", _fmt_bps)
        self._abs_rule("T10Y2Y_wow_bps", "curve_wow_bps", "10Y-2Y curve", _fmt_bps)
        for series in ("SOFR", "EFFR", "IORB"):
            self._abs_rule(f"{series}_wow_bps", "policy_rate_wow_bps", series, _fmt_bps)

        for key, label in (
            ("SOFR_minus_IORB_wow_bps", "SOFR-IORB"),
            ("SOFR_minus_EFFR_wow_bps", "SOFR-EFFR"),
        ):
            value = self._usable(key)
            if value is not None and value >= t["funding_spread_widen_bps"]:
                self.alerts.append(
                    Alert(key, f"{label} funding spread widened {_fmt_bps(value)}",
                          value, t["funding_spread_widen_bps"], "critical")
                )
        return self.alerts


def evaluate(snapshot: Snapshot, thresholds: dict[str, float]) -> list[Alert]:
    return AlertEvaluator(snapshot, thresholds).evaluate()


def regime_alerts(current, previous: dict | None, thresholds: dict[str, float]) -> list[Alert]:
    """Alerts that compare this run's regime against the previous run's."""
    out: list[Alert] = []
    if not previous:
        return out
    delta_threshold = thresholds.get("regime_score_delta", 2)
    old_score = previous.get("regime_score")
    old_label = previous.get("regime_label")
    if isinstance(old_score, (int, float)):
        delta = current.score - old_score
        if abs(delta) >= delta_threshold:
            out.append(
                Alert("regime_score_delta",
                      f"Regime score moved {delta:+d} to {current.score:+d}",
                      float(delta), float(delta_threshold), "warning")
            )
    if old_label and old_label != current.label:
        out.append(
            Alert("regime_label_change",
                  f"Regime changed from {old_label} to {current.label}",
                  0.0, 0.0, "warning")
        )
    return out
