"""Stablecoin and ECB parsing against recorded responses."""

from __future__ import annotations

import json
from datetime import date

from conftest import RecordedFetcher

from liquidity_sentinel.sources.ecb import fetch_ecb, parse_year_include
from liquidity_sentinel.sources.stablecoins import fetch_stablecoins, parse_payload


def test_stablecoin_supply_reads_nested_peg_values(fixtures):
    payload = json.loads((fixtures / "stablecoins.json").read_text())
    total, count, notes = parse_payload(payload)
    assert count == len(payload["peggedAssets"])
    assert total > 0
    assert notes == []


def test_stablecoin_parser_handles_flat_numeric_values():
    total, count, _ = parse_payload({"peggedAssets": [{"circulating": 1_000.0}]})
    assert (total, count) == (1_000.0, 1)


def test_stablecoin_parser_reports_a_shape_change_instead_of_returning_none():
    """The export's stablecoins_note.json recorded exactly this failure."""
    total, count, notes = parse_payload({"chains": [], "somethingElse": 1})
    assert total is None
    assert count == 0
    assert notes and "peggedAssets missing" in notes[0]


def test_stablecoin_parser_notes_unreadable_assets():
    payload = {"peggedAssets": [{"circulating": {"peggedUSD": 5.0}}, {"symbol": "X"}]}
    total, count, notes = parse_payload(payload)
    assert (total, count) == (5.0, 1)
    assert notes and "1 asset(s) without a readable supply" in notes[0]


def test_fetch_stablecoins_reports_invalid_json(cfg):
    result = fetch_stablecoins(cfg, fetcher=RecordedFetcher({"llama.fi": "not json"}))
    assert not result.ok
    assert "parse" in result.errors


def test_fetch_stablecoins_success(cfg, fixtures):
    body = (fixtures / "stablecoins.json").read_text()
    result = fetch_stablecoins(cfg, fetcher=RecordedFetcher({"llama.fi": body}))
    assert result.ok
    assert result.data["asset_count"] > 0


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
