"""Derived metrics: week-over-week changes, Fed net liquidity, funding spreads.

Two corrections to the original tracker live here.

*Alignment.* Fed net liquidity is ``WALCL - WTREGEN - RRPONTSYD``. WALCL and
WTREGEN are weekly (Wednesday); RRPONTSYD is daily. Combining each series' own
latest value mixes observation dates, so a Friday RRP print was being subtracted
from a Wednesday balance sheet. Components are now sampled as-of the weekly
anchor date.

*Comparison basis.* Week-over-week used to compare the current value against
whatever the previous successful run stored, so a skipped run silently turned a
one-week change into a multi-week change. Changes are now computed from the
observation history itself against the newest observation at least seven days
older than the latest one.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from .sources.fred import BALANCE_MILLIONS

# Series reported as a rate or spread in percent; changes are shown in bps.
PERCENT_SERIES = frozenset(
    {"BAMLC0A0CM", "BAMLH0A0HYM2", "DGS2", "DGS10", "T10Y2Y", "SOFR", "EFFR", "IORB"}
)
# Series already denominated in billions of dollars.
BILLIONS_SERIES = frozenset({"RRPONTSYD", "WM2NS"})
# Index-valued series reported as raw levels.
INDEX_SERIES = frozenset({"NFCI", "ANFCI"})

DISPLAY_NAME = {"WM2NS": "M2"}


def finite(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


@dataclass
class SeriesReading:
    """One series reduced to a latest observation and its week-prior comparison."""

    series: str
    latest_date: date
    latest_value: float
    prior_date: date | None = None
    prior_value: float | None = None
    age_days: int = 0
    stale: bool = False
    source: str = "cache"

    @property
    def change(self) -> float | None:
        if not finite(self.prior_value):
            return None
        return self.latest_value - self.prior_value

    @property
    def pct_change(self) -> float | None:
        if not finite(self.prior_value) or not self.prior_value:
            return None
        return (self.latest_value - self.prior_value) / self.prior_value


@dataclass
class Snapshot:
    """Everything one run knows, ready for alerting and reporting."""

    asof_date: str | None = None
    asof_prior_date: str | None = None
    values: dict[str, float | None] = field(default_factory=dict)
    dates: dict[str, str] = field(default_factory=dict)
    stale_series: list[str] = field(default_factory=list)
    fresh_series: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> float | None:
        value = self.values.get(key)
        return value if finite(value) else None


def value_asof(history: list[tuple[date, float]], target: date) -> tuple[date, float] | None:
    """Newest observation on or before *target*."""
    candidate = None
    for obs_date, value in history:
        if obs_date <= target:
            candidate = (obs_date, value)
        else:
            break
    return candidate


def week_prior(history: list[tuple[date, float]]) -> tuple[date, float] | None:
    """Newest observation at least seven days older than the latest one."""
    if len(history) < 2:
        return None
    target = history[-1][0] - timedelta(days=7)
    eligible = [p for p in history[:-1] if p[0] <= target]
    return eligible[-1] if eligible else history[-2]


def read_series(
    series: str,
    history: list[tuple[date, float]],
    *,
    today: date,
    stale_after_days: int,
    source: str = "cache",
) -> SeriesReading | None:
    if not history:
        return None
    latest_date, latest_value = history[-1]
    prior = week_prior(history)
    age = (today - latest_date).days
    return SeriesReading(
        series=series,
        latest_date=latest_date,
        latest_value=latest_value,
        prior_date=prior[0] if prior else None,
        prior_value=prior[1] if prior else None,
        age_days=age,
        stale=age > stale_after_days,
        source=source,
    )


def _emit(snapshot: Snapshot, reading: SeriesReading) -> None:
    """Write one reading into the snapshot using its natural units."""
    name = DISPLAY_NAME.get(reading.series, reading.series)
    change = reading.change

    if reading.series in BALANCE_MILLIONS:
        snapshot.values[f"{name}_bil"] = reading.latest_value / 1000.0
        snapshot.values[f"{name}_wow_bil"] = change / 1000.0 if finite(change) else None
    elif reading.series in BILLIONS_SERIES:
        snapshot.values[f"{name}_bil"] = reading.latest_value
        snapshot.values[f"{name}_wow_bil"] = change if finite(change) else None
        if reading.series == "WM2NS":
            snapshot.values["M2_wow_pct"] = reading.pct_change
    elif reading.series in PERCENT_SERIES:
        snapshot.values[f"{name}_pct"] = reading.latest_value
        snapshot.values[f"{name}_wow_bps"] = change * 100 if finite(change) else None
    elif reading.series in INDEX_SERIES:
        snapshot.values[name] = reading.latest_value
        snapshot.values[f"{name}_wow"] = change if finite(change) else None
    else:
        snapshot.values[name] = reading.latest_value
        snapshot.values[f"{name}_wow"] = change if finite(change) else None

    snapshot.dates[f"{name}_date"] = reading.latest_date.isoformat()
    snapshot.values[f"{name}_age_days"] = float(reading.age_days)
    (snapshot.stale_series if reading.stale else snapshot.fresh_series).append(reading.series)


def _net_liquidity(
    snapshot: Snapshot, histories: dict[str, list[tuple[date, float]]], readings: dict
) -> None:
    """Fed net liquidity, with components sampled as of the weekly anchor date."""
    walcl = readings.get("WALCL")
    if walcl is None:
        return
    anchor = walcl.latest_date

    def aligned(series: str) -> tuple[date, float] | None:
        return value_asof(histories.get(series, []), anchor)

    tga = aligned("WTREGEN")
    rrp = aligned("RRPONTSYD")
    if tga is None or rrp is None:
        snapshot.notes.append("net liquidity skipped: missing WTREGEN or RRPONTSYD")
        return

    latest = walcl.latest_value / 1000.0 - tga[1] / 1000.0 - rrp[1]
    snapshot.values["fed_net_liquidity_bil"] = latest
    snapshot.dates["fed_net_liquidity_date"] = anchor.isoformat()

    if walcl.prior_date is None:
        return
    prior_anchor = walcl.prior_date
    prior_tga = value_asof(histories.get("WTREGEN", []), prior_anchor)
    prior_rrp = value_asof(histories.get("RRPONTSYD", []), prior_anchor)
    if prior_tga is None or prior_rrp is None:
        return
    prior = walcl.prior_value / 1000.0 - prior_tga[1] / 1000.0 - prior_rrp[1]
    snapshot.values["fed_net_liquidity_wow_bil"] = latest - prior
    snapshot.dates["fed_net_liquidity_prior_date"] = prior_anchor.isoformat()


def _funding_spreads(snapshot: Snapshot) -> None:
    """SOFR relative to IORB and EFFR, in bps, with week-over-week change."""
    pairs = (("SOFR", "IORB", "SOFR_minus_IORB"), ("SOFR", "EFFR", "SOFR_minus_EFFR"))
    for base, other, name in pairs:
        a, b = snapshot.get(f"{base}_pct"), snapshot.get(f"{other}_pct")
        if a is None or b is None:
            continue
        snapshot.values[f"{name}_bps"] = (a - b) * 100
        da, db = snapshot.get(f"{base}_wow_bps"), snapshot.get(f"{other}_wow_bps")
        if da is not None and db is not None:
            snapshot.values[f"{name}_wow_bps"] = da - db


def build_snapshot(
    histories: dict[str, list[tuple[date, float]]],
    *,
    today: date,
    stale_after_days: int | Callable[[str], int],
    sources: dict[str, str] | None = None,
) -> Snapshot:
    """Reduce raw series histories into a single comparable snapshot.

    ``stale_after_days`` may be an int or a callable resolving one per series,
    because publication lag differs: WM2NS is weekly data released about four
    weeks late, while SOFR lands the next business day.
    """
    snapshot = Snapshot()
    sources = sources or {}
    readings: dict[str, SeriesReading] = {}
    limit_for = (
        stale_after_days if callable(stale_after_days) else (lambda _s: int(stale_after_days))
    )

    for series, history in histories.items():
        reading = read_series(
            series,
            history,
            today=today,
            stale_after_days=limit_for(series),
            source=sources.get(series, "cache"),
        )
        if reading is None:
            continue
        readings[series] = reading
        _emit(snapshot, reading)

    _net_liquidity(snapshot, histories, readings)
    _funding_spreads(snapshot)

    fresh_dates = [readings[s].latest_date for s in snapshot.fresh_series if s in readings]
    prior_dates = [
        readings[s].prior_date
        for s in snapshot.fresh_series
        if readings.get(s) and readings[s].prior_date
    ]
    if fresh_dates:
        snapshot.asof_date = max(fresh_dates).isoformat()
    if prior_dates:
        snapshot.asof_prior_date = max(prior_dates).isoformat()
    return snapshot


def apply_stablecoins(
    snapshot: Snapshot,
    total_usd: float | None,
    asset_count: int | None,
    history: list[tuple[date, float]] | None = None,
    observed_on: date | None = None,
) -> None:
    """Attach stablecoin supply and its week-over-week change.

    The change is measured against the newest stored total at least seven days
    old, not against the previous run. Running twice in one day therefore
    reports the real weekly move rather than a near-zero delta, and a skipped
    week is not silently relabelled as a one-week change.
    """
    if total_usd is None:
        snapshot.values["stablecoins_total_usd"] = None
        snapshot.values["stablecoins_wow_usd"] = None
        snapshot.values["stablecoins_wow_pct"] = None
        return

    snapshot.values["stablecoins_total_usd"] = total_usd
    snapshot.values["stablecoins_asset_count"] = float(asset_count or 0)

    prior = week_prior(history) if history and len(history) >= 2 else None
    if prior is None or not prior[1]:
        snapshot.values["stablecoins_wow_usd"] = None
        snapshot.values["stablecoins_wow_pct"] = None
        return

    prior_date, prior_total = prior
    snapshot.values["stablecoins_wow_usd"] = total_usd - prior_total
    snapshot.values["stablecoins_wow_pct"] = (total_usd - prior_total) / prior_total
    snapshot.dates["stablecoins_prior_date"] = prior_date.isoformat()

    # History can be sparse after a gap in runs. Say so rather than presenting a
    # multi-week move as a weekly one.
    if observed_on is not None:
        span = (observed_on - prior_date).days
        snapshot.values["stablecoins_change_days"] = float(span)
        if span > 10:
            snapshot.notes.append(
                f"stablecoin change measured over {span} days, not 7 "
                f"(no closer observation since {prior_date.isoformat()})"
            )
