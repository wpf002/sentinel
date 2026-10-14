"""CLI helpers that report state back to the user."""

from __future__ import annotations

from datetime import date

from liquidity_sentinel.cli import current_staleness


def test_staleness_is_measured_against_today_not_the_last_run(cfg, store):
    """A saved stale_series list was true when written; replaying it lies."""
    store.upsert_observations("SOFR", [(date(2026, 8, 26), 4.3)], "fred_csv")
    store.upsert_observations("WALCL", [(date(2026, 8, 26), 6_700_000.0)], "fred_csv")

    assert current_staleness(cfg, store, date(2026, 8, 31)) == {}

    stale = current_staleness(cfg, store, date(2026, 10, 14))
    assert stale == {"SOFR": 49, "WALCL": 49}


def test_staleness_honours_each_series_limit(cfg, store):
    """WM2NS gets 45 days, SOFR gets 10."""
    store.upsert_observations("WM2NS", [(date(2026, 8, 3), 23_207.7)], "fred_csv")
    store.upsert_observations("SOFR", [(date(2026, 8, 3), 4.3)], "fred_csv")

    stale = current_staleness(cfg, store, date(2026, 9, 1))
    assert "SOFR" in stale, "29 days old, limit 10"
    assert "WM2NS" not in stale, "29 days old, limit 45"


def test_staleness_uses_the_stablecoin_limit_for_the_aggregate(cfg, store):
    from liquidity_sentinel.pipeline import STABLECOIN_SERIES

    store.upsert_observations(STABLECOIN_SERIES, [(date(2026, 10, 1), 3.1e11)], "defillama")
    assert current_staleness(cfg, store, date(2026, 10, 8)) == {}
    assert current_staleness(cfg, store, date(2026, 10, 20)) == {STABLECOIN_SERIES: 19}


def test_staleness_sorts_worst_first(cfg, store):
    store.upsert_observations("SOFR", [(date(2026, 9, 1), 4.3)], "fred_csv")
    store.upsert_observations("DGS10", [(date(2026, 8, 1), 4.6)], "fred_csv")
    assert list(current_staleness(cfg, store, date(2026, 10, 14))) == ["DGS10", "SOFR"]


def test_empty_store_has_no_stale_series(cfg, store):
    assert current_staleness(cfg, store, date(2026, 10, 14)) == {}
