"""FRED parsing, with the ZIP regression front and centre."""

from __future__ import annotations

from datetime import date

from conftest import RecordedFetcher, make_response

from liquidity_sentinel.http import FetchError
from liquidity_sentinel.sources.fred import (
    fetch_fred,
    parse_api_json,
    parse_csv_series,
    parse_response,
)


def test_parses_single_series_csv(fixtures):
    text = (fixtures / "fred_walcl.csv").read_text()
    points = parse_csv_series(text, "WALCL")
    assert points
    assert all(isinstance(d, date) and isinstance(v, float) for d, v in points)
    assert points == sorted(points), "observations must be date-ordered"


def test_multiseries_response_is_a_zip_not_csv(fixtures):
    """The original bug: fredgraph returns a ZIP for mixed-frequency requests.

    Decoding it as text yields no rows, which the old runner reported as
    "FRED unavailable" even on an HTTP 200.
    """
    raw = (fixtures / "fred_multiseries.zip").read_bytes()
    response = make_response(raw)

    assert response.is_zip, "fixture must be the real archive response"
    parsed_as_text = parse_csv_series(response.text(), "WALCL")
    assert parsed_as_text == [], "text parse finds nothing -- the old bug"
    assert parse_response(response, "WALCL"), "ZIP-aware parse recovers the series"


def test_zip_members_are_merged_across_frequencies(fixtures):
    response = make_response((fixtures / "fred_multiseries.zip").read_bytes())
    members = response.zip_members(".csv")
    assert len(members) > 1, "archive splits series by observation frequency"
    points = parse_response(response, "WALCL")
    assert len(points) == len({d for d, _ in points}), "no duplicate dates after merge"


def test_parse_csv_ignores_missing_markers():
    text = "observation_date,SOFR\n2026-08-24,4.30\n2026-08-25,.\n2026-08-26,\n2026-08-27,4.32\n"
    assert parse_csv_series(text, "SOFR") == [(date(2026, 8, 24), 4.30), (date(2026, 8, 27), 4.32)]


def test_parse_csv_returns_empty_for_unknown_series():
    text = "observation_date,WALCL\n2026-08-24,100\n"
    assert parse_csv_series(text, "DGS10") == []


def test_parse_api_json():
    payload = {
        "observations": [
            {"date": "2026-08-24", "value": "4.30"},
            {"date": "2026-08-25", "value": "."},
            {"date": "2026-08-26", "value": "4.35"},
        ]
    }
    assert parse_api_json(payload, "SOFR") == [
        (date(2026, 8, 24), 4.30),
        (date(2026, 8, 26), 4.35),
    ]


def test_one_failing_series_does_not_sink_the_others(cfg, fixtures):
    """The blast-radius fix: 14 good series still report when 1 fails."""
    walcl = (fixtures / "fred_walcl.csv").read_text()

    class PartialFetcher:
        def __call__(self, url, http_cfg, **kwargs):
            if "id=DGS10" in url:
                raise FetchError(url, 4, "TimeoutError: read timed out")
            series = url.split("id=")[1]
            return make_response(walcl.replace("WALCL", series), url)

    result = fetch_fred(cfg, fetcher=PartialFetcher())
    assert "DGS10" in result.errors
    assert len(result.data) == 14
    assert result.ok is False, "a partial run is not a clean run"
    assert result.data["WALCL"], "unrelated series unaffected"


def test_api_strategy_falls_back_to_csv(cfg, fixtures):
    """A keyed API failure must degrade to the keyless CSV endpoint."""
    from dataclasses import replace

    keyed = replace(cfg, fred=replace(cfg.fred, api_key="test-key", strategy=("api", "csv")))
    walcl = (fixtures / "fred_walcl.csv").read_text()

    class ApiDown:
        def __call__(self, url, http_cfg, **kwargs):
            if "api.stlouisfed.org" in url:
                raise FetchError(url, 4, "HTTP 503")
            return make_response(walcl, url)

    result = fetch_fred(keyed, fetcher=ApiDown(), series=("WALCL",))
    assert result.ok
    assert result.labels["WALCL"] == "fred_csv"


def test_api_strategy_skipped_without_key(cfg, fixtures):
    walcl = (fixtures / "fred_walcl.csv").read_text()
    fetcher = RecordedFetcher({"fredgraph.csv": walcl})
    result = fetch_fred(cfg, fetcher=fetcher, series=("WALCL",))
    assert result.ok
    assert not any("api.stlouisfed.org" in c for c in fetcher.calls)
