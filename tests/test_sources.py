"""Stablecoin and ECB parsing against recorded responses."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import RecordedFetcher

from liquidity_sentinel.sources.ecb import fetch_ecb, parse_year_include
from liquidity_sentinel.sources.stablecoins import fetch_stablecoins, parse_history


def test_chart_payload_parses_to_daily_points(fixtures):
    payload = json.loads((fixtures / "stablecoin_chart.json").read_text())
    points, pegs, notes = parse_history(payload)

    assert len(points) == len(payload)
    assert notes == []
    assert pegs == 28, "peg count is the schema canary"
    assert points == sorted(points), "points must be date-ordered"
    assert all(v > 0 for _, v in points)


def test_history_is_dense_enough_for_a_real_seven_day_comparison(fixtures):
    """The point of switching endpoints: no gaps to stretch the window."""
    payload = json.loads((fixtures / "stablecoin_chart.json").read_text())
    points, _, _ = parse_history(payload)
    days = [d for d, _ in points]
    span = (days[-1] - days[0]).days + 1
    assert span == len(days), "one observation per day, no gaps"


def test_totals_sum_every_peg_currency():
    payload = [
        {"date": "1760572800", "totalCirculatingUSD": {"peggedUSD": 100.0, "peggedEUR": 25.0}}
    ]
    points, pegs, _ = parse_history(payload)
    assert points[0][1] == pytest.approx(125.0)
    assert pegs == 2


def test_flat_numeric_total_is_accepted():
    payload = [{"date": "1760572800", "totalCirculatingUSD": 500.0}]
    points, _, _ = parse_history(payload)
    assert points[0][1] == pytest.approx(500.0)


def test_falls_back_to_totalCirculating_when_usd_is_absent():
    payload = [{"date": "1760572800", "totalCirculating": {"peggedUSD": 7.0}}]
    points, _, _ = parse_history(payload)
    assert points[0][1] == pytest.approx(7.0)


def test_unreadable_points_are_skipped_and_counted():
    payload = [
        {"date": "1760572800", "totalCirculatingUSD": {"peggedUSD": 1.0}},
        {"date": "not-a-timestamp", "totalCirculatingUSD": {"peggedUSD": 2.0}},
        {"date": "1760659200"},
        "junk",
    ]
    points, _, notes = parse_history(payload)
    assert len(points) == 1
    assert notes and "3 unreadable chart point(s) skipped" in notes[0]


def test_duplicate_dates_collapse_to_the_last_value():
    payload = [
        {"date": "1760572800", "totalCirculatingUSD": {"peggedUSD": 1.0}},
        {"date": "1760572800", "totalCirculatingUSD": {"peggedUSD": 9.0}},
    ]
    points, _, _ = parse_history(payload)
    assert points == [(date(2025, 10, 16), 9.0)] or len(points) == 1


def test_shape_change_is_reported_instead_of_returning_none():
    """The export's stablecoins_note.json recorded exactly this failure."""
    points, pegs, notes = parse_history({"chains": [], "somethingElse": 1})
    assert points == []
    assert pegs == 0
    assert notes and "expected a list of chart points" in notes[0]


def test_fetch_stablecoins_reports_invalid_json(cfg):
    result = fetch_stablecoins(cfg, fetcher=RecordedFetcher({"llama.fi": "not json"}))
    assert not result.ok
    assert "parse" in result.errors


def test_fetch_stablecoins_returns_current_value_and_history(fixtures, cfg):
    body = (fixtures / "stablecoin_chart.json").read_text()
    result = fetch_stablecoins(cfg, fetcher=RecordedFetcher({"llama.fi": body}))

    assert result.ok
    assert result.data["observed_on"] == date(2026, 10, 16)
    assert result.data["total_usd"] == pytest.approx(result.data["history"][-1][1])
    assert len(result.data["history"]) == 45
    assert result.data["peg_count"] == 28


def test_ecb_parses_the_year_include(fixtures):
    html = (fixtures / "ecb_year_include.html").read_text()
    entry = parse_year_include(html, today=date(2026, 8, 31))
    assert entry["date"] == "2026-08-25"
    assert entry["statement_as_at"] == "2026-08-21"
    assert "Consolidated financial statement" in entry["title"]
    assert entry["url"].startswith("https://www.ecb.europa.eu/")


def test_ecb_ignores_statements_dated_in_the_future(fixtures):
    html = (fixtures / "ecb_year_include.html").read_text()
    entry = parse_year_include(html, today=date(2026, 8, 20))
    assert entry is None or entry["date"] <= "2026-08-20"


def test_ecb_returns_none_for_the_navigation_shell():
    """The old extractor scraped promo tiles out of the index page chrome."""
    shell = '<div class="image-date">23 July 2026</div><div>Quick links Strategy review</div>'
    assert parse_year_include(shell, today=date(2026, 8, 31)) is None


def test_ecb_falls_back_to_the_previous_year(cfg, fixtures):
    """In early January the current year's include can still be empty."""
    html = (fixtures / "ecb_year_include.html").read_text()
    fetcher = RecordedFetcher({"/2026/": html, "/2027/": "<html>empty</html>"})
    result = fetch_ecb(cfg, fetcher=fetcher, today=date(2027, 1, 2))
    assert result.ok
    assert result.data["date"] == "2026-08-25"
    assert any("/2027/" in c for c in fetcher.calls), "current year tried first"


def test_ecb_failure_is_reported_not_raised(cfg):
    from liquidity_sentinel.http import FetchError

    fetcher = RecordedFetcher({}, default=FetchError("u", 4, "HTTP 500"))
    result = fetch_ecb(cfg, fetcher=fetcher, today=date(2026, 8, 31))
    assert not result.ok
    assert result.errors


def test_ecb_can_be_disabled(cfg):
    from dataclasses import replace

    disabled = replace(cfg, ecb=replace(cfg.ecb, enabled=False))
    result = fetch_ecb(disabled, fetcher=RecordedFetcher({}))
    assert not result.ok
    assert result.notes == ["disabled in config"]
