"""Snapshot construction: units, alignment, week-over-week basis, staleness."""

from __future__ import annotations

from datetime import date

import pytest

from liquidity_sentinel.metrics import (
    build_snapshot,
    read_series,
    value_asof,
    week_prior,
)

WEEKLY = [
    (date(2026, 8, 5), 6_700_000.0),
    (date(2026, 8, 12), 6_710_000.0),
    (date(2026, 8, 19), 6_745_699.0),
    (date(2026, 8, 26), 6_730_912.0),
]


def test_week_prior_skips_intraweek_observations():
    daily = [(date(2026, 8, d), float(d)) for d in range(17, 27)]
    prior = week_prior(daily)
    assert prior[0] == date(2026, 8, 19), "seven days before the 26th"


def test_week_prior_falls_back_to_previous_point_when_history_is_short():
    short = [(date(2026, 8, 25), 1.0), (date(2026, 8, 26), 2.0)]
    assert week_prior(short) == (date(2026, 8, 25), 1.0)


def test_week_prior_needs_two_points():
    assert week_prior([(date(2026, 8, 26), 1.0)]) is None


def test_value_asof_returns_newest_on_or_before_target():
    assert value_asof(WEEKLY, date(2026, 8, 20)) == (date(2026, 8, 19), 6_745_699.0)
    assert value_asof(WEEKLY, date(2026, 8, 26)) == (date(2026, 8, 26), 6_730_912.0)
    assert value_asof(WEEKLY, date(2026, 8, 1)) is None


def test_balance_sheet_series_convert_millions_to_billions():
    snap = build_snapshot({"WALCL": WEEKLY}, today=date(2026, 8, 31), stale_after_days=14)
    assert snap.get("WALCL_bil") == pytest.approx(6730.912)
    assert snap.get("WALCL_wow_bil") == pytest.approx(-14.787, abs=1e-3)


def test_percent_series_report_changes_in_bps():
    sofr = [(date(2026, 8, 19), 4.30), (date(2026, 8, 26), 4.42)]
    snap = build_snapshot({"SOFR": sofr}, today=date(2026, 8, 31), stale_after_days=14)
    assert snap.get("SOFR_pct") == pytest.approx(4.42)
    assert snap.get("SOFR_wow_bps") == pytest.approx(12.0)


def test_net_liquidity_aligns_components_to_the_weekly_anchor():
    """RRP is daily; using its own latest print would mix observation dates."""
    walcl = [(date(2026, 8, 19), 6_745_699.0), (date(2026, 8, 26), 6_730_912.0)]
    tga = [(date(2026, 8, 19), 900_000.0), (date(2026, 8, 26), 950_700.0)]
    rrp = [
        (date(2026, 8, 26), 0.500),   # the anchor date
        (date(2026, 8, 27), 0.456),
        (date(2026, 8, 28), 0.175),   # a later daily print that must NOT be used
    ]
    snap = build_snapshot(
        {"WALCL": walcl, "WTREGEN": tga, "RRPONTSYD": rrp},
        today=date(2026, 8, 31),
        stale_after_days=14,
    )
    expected = 6730.912 - 950.700 - 0.500
    assert snap.get("fed_net_liquidity_bil") == pytest.approx(expected)
    assert snap.dates["fed_net_liquidity_date"] == "2026-08-26"


def test_net_liquidity_skipped_when_a_component_is_missing():
    snap = build_snapshot({"WALCL": WEEKLY}, today=date(2026, 8, 31), stale_after_days=14)
    assert snap.get("fed_net_liquidity_bil") is None
    assert any("net liquidity skipped" in n for n in snap.notes)


def test_funding_spreads_computed_from_levels():
    snap = build_snapshot(
        {
            "SOFR": [(date(2026, 8, 19), 4.30), (date(2026, 8, 26), 4.40)],
            "IORB": [(date(2026, 8, 19), 4.40), (date(2026, 8, 26), 4.40)],
        },
        today=date(2026, 8, 31),
        stale_after_days=14,
    )
    assert snap.get("SOFR_minus_IORB_bps") == pytest.approx(0.0)
    assert snap.get("SOFR_minus_IORB_wow_bps") == pytest.approx(10.0)


def test_staleness_is_per_series_via_callable():
    """M2 lags ~4 weeks by design; a flat limit would flag it every week."""
    m2 = [(date(2026, 7, 27), 23_043.3), (date(2026, 8, 3), 23_207.7)]
    sofr = [(date(2026, 8, 19), 4.30), (date(2026, 8, 26), 4.40)]
    limits = {"WM2NS": 45, "SOFR": 10}
    snap = build_snapshot(
        {"WM2NS": m2, "SOFR": sofr},
        today=date(2026, 8, 31),
        stale_after_days=lambda s: limits[s],
    )
    assert "WM2NS" in snap.fresh_series, "28 days old but within its 45-day limit"
    assert "SOFR" in snap.fresh_series

    strict = build_snapshot(
        {"WM2NS": m2}, today=date(2026, 8, 31), stale_after_days=21
    )
    assert "WM2NS" in strict.stale_series, "a flat 21-day limit misreads M2"


def test_asof_reflects_fresh_series_only():
    fresh = [(date(2026, 8, 19), 1.0), (date(2026, 8, 26), 2.0)]
    stale = [(date(2026, 1, 5), 1.0), (date(2026, 1, 12), 2.0)]
    snap = build_snapshot(
        {"SOFR": fresh, "NFCI": stale}, today=date(2026, 8, 31), stale_after_days=14
    )
    assert snap.asof_date == "2026-08-26"
    assert snap.stale_series == ["NFCI"]


def test_read_series_reports_age():
    reading = read_series("WALCL", WEEKLY, today=date(2026, 8, 31), stale_after_days=14)
    assert reading.age_days == 5
    assert reading.stale is False
    assert reading.change == pytest.approx(-14_787.0)


def test_read_series_handles_empty_history():
    assert read_series("WALCL", [], today=date(2026, 8, 31), stale_after_days=14) is None


def test_stablecoin_change_uses_the_week_prior_observation():
    """Not the previous run: two runs in one day must not report a ~zero move."""
    from liquidity_sentinel.metrics import Snapshot, apply_stablecoins

    history = [
        (date(2026, 8, 17), 300e9),
        (date(2026, 8, 24), 310e9),
        (date(2026, 8, 31), 312e9),
    ]
    snap = Snapshot()
    apply_stablecoins(snap, 312e9, 400, history, observed_on=date(2026, 8, 31))
    assert snap.get("stablecoins_wow_usd") == pytest.approx(2e9)
    assert snap.dates["stablecoins_prior_date"] == "2026-08-24"
    assert snap.get("stablecoins_change_days") == pytest.approx(7)


def test_stablecoin_change_flags_a_wider_than_weekly_window():
    """Sparse history must not present a multi-week move as a weekly one."""
    from liquidity_sentinel.metrics import Snapshot, apply_stablecoins

    history = [(date(2026, 8, 14), 307e9), (date(2026, 8, 31), 313e9)]
    snap = Snapshot()
    apply_stablecoins(snap, 313e9, 400, history, observed_on=date(2026, 8, 31))
    assert snap.get("stablecoins_change_days") == pytest.approx(17)
    assert any("17 days, not 7" in n for n in snap.notes)


def test_stablecoin_change_absent_without_history():
    from liquidity_sentinel.metrics import Snapshot, apply_stablecoins

    snap = Snapshot()
    apply_stablecoins(snap, 313e9, 400, [(date(2026, 8, 31), 313e9)], observed_on=date(2026, 8, 31))
    assert snap.get("stablecoins_total_usd") == pytest.approx(313e9)
    assert snap.get("stablecoins_wow_usd") is None
