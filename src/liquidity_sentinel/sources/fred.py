"""FRED data acquisition.

The previous implementation issued three multi-series ``fredgraph.csv`` requests
and parsed the bytes as CSV. That endpoint returns a **ZIP archive** whenever the
requested ids span more than one observation frequency -- which all three groups
did. The parse therefore produced zero rows on every run, and the tracker
reported FRED as unavailable even when the HTTP call succeeded.

The fix is to fetch one series at a time:

* single-frequency responses are always plain CSV, so the ZIP case disappears;
* payloads shrink from ~600 KB to ~20 KB, which is what made the pulls time out;
* one failing series no longer invalidates the other fourteen.

``fetch_fred`` tries the keyed JSON API first when ``FRED_API_KEY`` is present and
falls back to the keyless CSV endpoint. A ZIP response is still unpacked rather
than misread, so a future FRED change cannot silently reintroduce the old bug.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import UTC, date, datetime, timedelta

from ..config import Config
from ..http import FetchError, Response, fetch
from .base import SourceResult

CSV_ENDPOINT = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
API_ENDPOINT = (
    "https://api.stlouisfed.org/fred/series/observations"
    "?series_id={series}&api_key={key}&file_type=json&observation_start={start}"
)

# Series the tracker depends on, grouped for reporting only -- each is fetched
# independently.
FRED_SERIES: dict[str, tuple[str, ...]] = {
    "liquidity_m2": ("WALCL", "WRESBAL", "WTREGEN", "RRPONTSYD", "WM2NS"),
    "spreads_conditions": ("BAMLC0A0CM", "BAMLH0A0HYM2", "NFCI", "ANFCI"),
    "rates_funding": ("DGS2", "DGS10", "T10Y2Y", "SOFR", "EFFR", "IORB"),
}

ALL_SERIES: tuple[str, ...] = tuple(s for group in FRED_SERIES.values() for s in group)

# WALCL, WRESBAL and WTREGEN are published in millions of dollars.
BALANCE_MILLIONS = frozenset({"WALCL", "WRESBAL", "WTREGEN"})

MISSING = {"", ".", "NaN", "nan", None}


def group_for(series: str) -> str:
    for group, members in FRED_SERIES.items():
        if series in members:
            return group
    return "other"


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def parse_csv_series(text: str, series: str) -> list[tuple[date, float]]:
    """Parse a single-series fredgraph CSV into sorted ``(date, value)`` pairs."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    date_field = next(
        (f for f in reader.fieldnames if f.strip().lower() in {"observation_date", "date"}), None
    )
    if date_field is None:
        return []
    # A single-series pull names the value column after the series, but the
    # frequency-split members of a ZIP response may carry several columns.
    value_field = next((f for f in reader.fieldnames if f.strip().upper() == series.upper()), None)
    if value_field is None:
        return []

    points: list[tuple[date, float]] = []
    for row in reader:
        raw_date = (row.get(date_field) or "").strip()
        raw_value = (row.get(value_field) or "").strip()
        if not raw_date or raw_value in MISSING:
            continue
        try:
            obs_date = date.fromisoformat(raw_date)
            value = float(raw_value)
        except ValueError:
            continue
        if _finite(value):
            points.append((obs_date, value))
    points.sort()
    return points


def parse_response(response: Response, series: str) -> list[tuple[date, float]]:
    """Parse a CSV response, transparently unpacking a ZIP archive if present."""
    if not response.is_zip:
        return parse_csv_series(response.text(), series)
    merged: dict[date, float] = {}
    for text in response.zip_members(".csv").values():
        for obs_date, value in parse_csv_series(text, series):
            merged[obs_date] = value
    return sorted(merged.items())


def parse_api_json(payload: dict, series: str) -> list[tuple[date, float]]:
    """Parse the keyed FRED API observations payload."""
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return []
    points: list[tuple[date, float]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("date")
        raw_value = item.get("value")
        if not raw_date or raw_value in MISSING:
            continue
        try:
            obs_date = date.fromisoformat(raw_date)
            value = float(raw_value)
        except (ValueError, TypeError):
            continue
        if _finite(value):
            points.append((obs_date, value))
    points.sort()
    return points


def _fetch_one(series: str, cfg: Config, fetcher) -> tuple[list[tuple[date, float]], str, str]:
    """Try each configured strategy for one series.

    Returns ``(points, source_label, error)``; ``error`` is empty on success.
    """
    errors: list[str] = []
    start = (datetime.now(UTC).date() - timedelta(days=cfg.fred.history_days)).isoformat()

    for strategy in cfg.fred.strategy:
        if strategy == "api":
            if not cfg.fred.api_key:
                continue
            url = API_ENDPOINT.format(series=series, key=cfg.fred.api_key, start=start)
            label = "fred_api"
            parser = lambda r: parse_api_json(json.loads(r.text()), series)  # noqa: E731
        elif strategy == "csv":
            url = CSV_ENDPOINT.format(series=series)
            label = "fred_csv"
            parser = lambda r: parse_response(r, series)  # noqa: E731
        else:
            continue

        try:
            response = fetcher(url, cfg.http)
            points = parser(response)
        except FetchError as exc:
            errors.append(f"{label}: {exc.last_error}")
            continue
        except Exception as exc:  # noqa: BLE001 - parse failures are reported, not raised
            errors.append(f"{label}: parse {type(exc).__name__}: {exc}")
            continue

        if points:
            return points, label, ""
        errors.append(f"{label}: no observations parsed")

    return [], "", "; ".join(errors) or "no strategy configured"


def fetch_fred(cfg: Config, *, fetcher=fetch, series: tuple[str, ...] = ALL_SERIES) -> SourceResult:
    """Fetch every FRED series independently.

    A failure is recorded per series; the run continues with whatever landed.
    """
    result = SourceResult(name="fred")
    data: dict[str, list[tuple[date, float]]] = {}
    labels: dict[str, str] = {}

    for name in series:
        points, label, error = _fetch_one(name, cfg, fetcher)
        result.attempts += 1
        if error:
            result.errors[name] = error
            continue
        data[name] = points
        labels[name] = label

    result.data = data
    result.notes.append(f"{len(data)}/{len(series)} series retrieved")
    result.ok = bool(data) and not result.errors
    result.labels = labels
    return result
