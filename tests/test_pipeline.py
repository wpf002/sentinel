"""End-to-end runs against recorded responses. No network."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from conftest import RecordedFetcher, make_response

from liquidity_sentinel.http import FetchError
from liquidity_sentinel.migrate import load_wide_csv
from liquidity_sentinel.pipeline import in_window, run
from liquidity_sentinel.sources.fred import ALL_SERIES

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
TODAY = date(2026, 8, 31)


def series_csv(series: str, points: list[tuple[str, float]]) -> str:
    rows = "\n".join(f"{d},{v}" for d, v in points)
    return f"observation_date,{series}\n{rows}\n"


def full_fred_routes() -> dict[str, str]:
    """One healthy CSV per series, two weekly observations each."""
    routes = {}
    for name in ALL_SERIES:
        base = 6_700_000.0 if name in {"WALCL", "WRESBAL", "WTREGEN"} else 4.0
        routes[f"id={name}"] = series_csv(
            name, [("2026-08-19", base), ("2026-08-26", base * 1.01)]
        )
    return routes


@pytest.fixture
def local_cfg(cfg):
    """No console sink, so tests do not print run bodies."""
    return replace(cfg, notify=replace(cfg.notify, sinks=("file",)))


def test_full_run_records_every_series(local_cfg, store, fixtures):
    stable = (fixtures / "stablecoins.json").read_text()
    ecb = (fixtures / "ecb_year_include.html").read_text()
    fetcher = RecordedFetcher({**full_fred_routes(), "llama.fi": stable, "/2026/": ecb})

    result = run(local_cfg, store=store, force=True, fetcher=fetcher, today=TODAY, now=NOW)

    assert result.ran
    assert result.payload["source_status"]["fred"] == f"{len(ALL_SERIES)}/{len(ALL_SERIES)} series"
    assert len(result.snapshot.fresh_series) == len(ALL_SERIES)
    # 15 FRED series x 2 observations, plus the stablecoin aggregate.
    assert store.observation_count() == len(ALL_SERIES) * 2 + 1
    assert store.series_history("STABLECOINS_TOTAL"), "aggregate stored as a series"


def test_zip_response_no_longer_reads_as_unavailable(local_cfg, store, fixtures):
    """The exact regression: HTTP 200 + ZIP body used to mean 'FRED down'."""
    archive = (fixtures / "fred_multiseries.zip").read_bytes()
    stable = (fixtures / "stablecoins.json").read_text()

    class ZipFetcher:
        def __call__(self, url, http_cfg, **kwargs):
            if "llama.fi" in url:
                return make_response(stable, url)
            if "ecb.europa.eu" in url:
                raise FetchError(url, 1, "HTTP 404")
            return make_response(archive, url)

    result = run(local_cfg, store=store, force=True, fetcher=ZipFetcher(), today=TODAY, now=NOW)

    assert result.snapshot.get("WALCL_bil") is not None, "series recovered from the archive"
    assert result.snapshot.fresh_series, "the old code reported zero series here"
    assert store.observation_count() > 0


def test_one_dead_series_does_not_freeze_the_run(local_cfg, store, fixtures):
    """The blast-radius fix, end to end."""
    routes = full_fred_routes()
    stable = (fixtures / "stablecoins.json").read_text()

    class PartialFetcher:
        def __call__(self, url, http_cfg, **kwargs):
            if "id=DGS10" in url:
                raise FetchError(url, 4, "TimeoutError: read timed out")
            if "llama.fi" in url:
                return make_response(stable, url)
            if "ecb.europa.eu" in url:
                raise FetchError(url, 1, "HTTP 404")
            for needle, body in routes.items():
                if needle in url:
                    return make_response(body, url)
            raise AssertionError(url)

    result = run(local_cfg, store=store, force=True, fetcher=PartialFetcher(), today=TODAY, now=NOW)

    assert result.ran
    assert len(result.snapshot.fresh_series) == len(ALL_SERIES) - 1
    assert result.snapshot.get("fed_net_liquidity_bil") is not None
    assert any("DGS10" in n for n in result.payload["notes"])


def test_total_fred_outage_still_produces_a_stablecoin_run(local_cfg, store, fixtures):
    stable = (fixtures / "stablecoins.json").read_text()

    class FredDown:
        def __call__(self, url, http_cfg, **kwargs):
            if "llama.fi" in url:
                return make_response(stable, url)
            raise FetchError(url, 4, "TimeoutError: read timed out")

    result = run(local_cfg, store=store, force=True, fetcher=FredDown(), today=TODAY, now=NOW)

    assert result.ran
    assert result.snapshot.get("stablecoins_total_usd") is not None
    assert result.payload["source_status"]["fred"].startswith("0/")


def test_cached_series_survive_a_failed_fetch(local_cfg, store, fixtures):
    """Week two: FRED is down, but the mirror still answers."""
    stable = (fixtures / "stablecoins.json").read_text()
    healthy = RecordedFetcher(
        {**full_fred_routes(), "llama.fi": stable}, default=FetchError("u", 1, "HTTP 404")
    )
    run(local_cfg, store=store, force=True, fetcher=healthy, today=TODAY, now=NOW)
    assert store.observation_count() == len(ALL_SERIES) * 2 + 1

    class AllDown:
        def __call__(self, url, http_cfg, **kwargs):
            if "llama.fi" in url:
                return make_response(stable, url)
            raise FetchError(url, 4, "TimeoutError")

    later = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    result = run(
        local_cfg, store=store, force=True, fetcher=AllDown(),
        today=date(2026, 9, 7), now=later,
    )
    assert result.snapshot.get("WALCL_bil") is not None, "served from the mirror"
    assert "WALCL" in result.snapshot.fresh_series, "12 days old, within its 14-day limit"


def test_stale_cache_is_flagged_not_silently_reused(local_cfg, store, fixtures):
    stable = (fixtures / "stablecoins.json").read_text()
    healthy = RecordedFetcher(
        {**full_fred_routes(), "llama.fi": stable}, default=FetchError("u", 1, "HTTP 404")
    )
    run(local_cfg, store=store, force=True, fetcher=healthy, today=TODAY, now=NOW)

    class AllDown:
        def __call__(self, url, http_cfg, **kwargs):
            if "llama.fi" in url:
                return make_response(stable, url)
            raise FetchError(url, 4, "TimeoutError")

    much_later = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)
    result = run(
        local_cfg, store=store, force=True, fetcher=AllDown(),
        today=date(2026, 11, 30), now=much_later,
    )
    assert set(result.snapshot.stale_series) == set(ALL_SERIES)
    assert result.snapshot.fresh_series == []
    assert result.payload["asof_date"] is None, "a run with no fresh data claims no as-of date"


def test_dry_run_writes_nothing(local_cfg, store, fixtures):
    stable = (fixtures / "stablecoins.json").read_text()
    fetcher = RecordedFetcher(
        {**full_fred_routes(), "llama.fi": stable}, default=FetchError("u", 1, "x")
    )
    result = run(
        local_cfg, store=store, force=True, dry_run=True, fetcher=fetcher, today=TODAY, now=NOW
    )
    assert result.ran
    assert store.last_run() is None, "dry run must not record"


def test_schedule_window_blocks_off_schedule_runs(cfg, store):
    enforced = replace(cfg, run=replace(cfg.run, enforce_window=True))
    # 2026-08-31 is a Monday; the window is Friday 07:00 America/Chicago.
    result = run(enforced, store=store, fetcher=RecordedFetcher({}), today=TODAY, now=NOW)
    assert not result.ran
    assert result.reason == "outside scheduled window"


def test_in_window_matches_the_configured_slot(cfg):
    friday_7am_ct = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)  # 07:00 CDT
    assert in_window(cfg, friday_7am_ct)
    assert not in_window(cfg, datetime(2026, 9, 4, 20, 0, tzinfo=UTC))
    assert not in_window(cfg, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))


def test_run_payload_is_json_serialisable(local_cfg, store, fixtures):
    stable = (fixtures / "stablecoins.json").read_text()
    fetcher = RecordedFetcher(
        {**full_fred_routes(), "llama.fi": stable}, default=FetchError("u", 1, "x")
    )
    result = run(local_cfg, store=store, force=True, fetcher=fetcher, today=TODAY, now=NOW)
    json.dumps(result.payload)  # must not raise


def test_migrate_parses_the_exported_wide_history():
    text = "DATE,WALCL,SOFR\n2026-08-19,100,4.3\n2026-08-26,,4.4\n2026-08-27,102,.\n"
    parsed = load_wide_csv(text)
    assert parsed["WALCL"] == [(date(2026, 8, 19), 100.0), (date(2026, 8, 27), 102.0)]
    assert parsed["SOFR"] == [(date(2026, 8, 19), 4.3), (date(2026, 8, 26), 4.4)]


def test_dry_run_writes_nothing_at_all(local_cfg, store, fixtures):
    """--dry-run must not touch observations, source health or the run log."""
    stable = (fixtures / "stablecoins.json").read_text()
    ecb = (fixtures / "ecb_year_include.html").read_text()
    fetcher = RecordedFetcher({**full_fred_routes(), "llama.fi": stable, "/2026/": ecb})

    result = run(
        local_cfg, store=store, force=True, dry_run=True, fetcher=fetcher, today=TODAY, now=NOW
    )

    assert result.ran
    assert store.observation_count() == 0, "a dry run persisted observations"
    assert store.health() == [], "a dry run marked source health"
    assert store.last_run() is None, "a dry run recorded a run"


def test_dry_run_still_evaluates_fetched_data(local_cfg, store, fixtures):
    """Reading nothing from disk must not mean reporting nothing."""
    stable = (fixtures / "stablecoins.json").read_text()
    ecb = (fixtures / "ecb_year_include.html").read_text()
    fetcher = RecordedFetcher({**full_fred_routes(), "llama.fi": stable, "/2026/": ecb})

    result = run(
        local_cfg, store=store, force=True, dry_run=True, fetcher=fetcher, today=TODAY, now=NOW
    )

    assert len(result.snapshot.fresh_series) == len(ALL_SERIES)
    assert result.snapshot.get("WALCL_bil") is not None
    assert result.snapshot.get("fed_net_liquidity_bil") is not None
    assert result.payload["asof_date"] == "2026-08-26"


def test_dry_run_merges_over_cached_history(local_cfg, store, fixtures):
    """A dry run after a real run sees both the mirror and the new fetch."""
    stable = (fixtures / "stablecoins.json").read_text()
    healthy = RecordedFetcher(
        {**full_fred_routes(), "llama.fi": stable}, default=FetchError("u", 1, "HTTP 404")
    )
    run(local_cfg, store=store, force=True, fetcher=healthy, today=TODAY, now=NOW)
    baseline = store.observation_count()

    later_routes = {
        f"id={name}": series_csv(name, [("2026-09-02", 9.0)]) for name in ALL_SERIES
    }
    fetcher = RecordedFetcher({**later_routes, "llama.fi": stable}, default=FetchError("u", 1, "x"))
    result = run(
        local_cfg, store=store, force=True, dry_run=True, fetcher=fetcher,
        today=date(2026, 9, 2), now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
    )

    assert store.observation_count() == baseline, "dry run must not grow the mirror"
    assert result.payload["asof_date"] == "2026-09-02", "but it must see the new observation"
