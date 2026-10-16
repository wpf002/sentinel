"""DefiLlama aggregate stablecoin supply.

This is the one source that has never failed, so it carries the weekly signal on
its own when FRED is degraded.

It reads ``/stablecoincharts/all``, which returns the full daily history of
aggregate supply, rather than ``/stablecoins``, which returns only a current
per-asset snapshot. Two reasons:

*History.* With a snapshot endpoint, the only stablecoin history the tracker had
was one point per run. A 44-day gap between runs produced a "week-over-week"
figure measured over 44 days. The chart endpoint carries 3,244 daily points back
to 2017-11-29, so the seven-day comparison is always a real seven days.

*One series.* The two endpoints do not agree: on 2026-10-14 the chart endpoint
reported $307.80B against the snapshot's $308.67B, a 0.28% difference in
aggregation. Backfilling from one and comparing against the other would inject a
phantom $0.87B move at the join. Reading both the current value and the history
from the same endpoint removes the seam.

The totals are summed across every peg currency (``peggedUSD``, ``peggedEUR``,
``peggedJPY``, ...), matching what the previous per-asset sum did.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime

from ..config import Config
from ..http import FetchError, fetch
from .base import SourceResult

SERIES = "STABLECOINS_TOTAL"
TOTAL_KEYS = ("totalCirculatingUSD", "totalCirculating")


def _sum_pegs(node: object) -> float | None:
    """Sum every numeric peg value in a ``totalCirculatingUSD`` node."""
    if isinstance(node, (int, float)):
        value = float(node)
        return value if math.isfinite(value) else None
    if not isinstance(node, dict):
        return None
    total = 0.0
    found = False
    for value in node.values():
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            total += float(value)
            found = True
    return total if found else None


def _point_date(raw: object) -> date | None:
    """Chart points carry a Unix timestamp, usually as a string."""
    try:
        return datetime.fromtimestamp(int(raw), UTC).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_history(payload: object) -> tuple[list[tuple[date, float]], int, list[str]]:
    """Return ``(points, peg_count, notes)`` from a ``/stablecoincharts`` payload.

    ``peg_count`` is the number of peg currencies in the newest point. It is the
    schema canary: a sudden drop means the upstream shape changed, which is the
    failure ``stablecoins_note.json`` recorded against the old parser.
    """
    notes: list[str] = []
    if not isinstance(payload, list):
        kind = type(payload).__name__
        keys = sorted(payload.keys())[:10] if isinstance(payload, dict) else []
        notes.append(f"expected a list of chart points, got {kind}; keys: {keys}")
        return [], 0, notes

    points: dict[date, float] = {}
    skipped = 0
    peg_count = 0
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        observed = _point_date(item.get("date"))
        node = next((item[k] for k in TOTAL_KEYS if k in item), None)
        total = _sum_pegs(node)
        if observed is None or total is None:
            skipped += 1
            continue
        points[observed] = total
        if isinstance(node, dict):
            peg_count = len(node)

    if skipped:
        notes.append(f"{skipped} unreadable chart point(s) skipped")
    if not points:
        notes.append("no usable chart points found")
    return sorted(points.items()), peg_count, notes


def fetch_stablecoins(cfg: Config, *, fetcher=fetch) -> SourceResult:
    """Fetch aggregate supply plus its full daily history in one request."""
    result = SourceResult(name="stablecoins")
    try:
        response = fetcher(cfg.stablecoins.url, cfg.http)
    except FetchError as exc:
        result.errors["fetch"] = exc.last_error
        result.attempts = exc.attempts
        return result

    result.attempts = response.attempts
    result.elapsed_seconds = response.elapsed_seconds
    try:
        payload = json.loads(response.text())
    except json.JSONDecodeError as exc:
        result.errors["parse"] = f"invalid JSON: {exc}"
        return result

    points, peg_count, notes = parse_history(payload)
    result.notes.extend(notes)
    if not points:
        result.errors["parse"] = "no stablecoin supply values found"
        return result

    latest_date, latest_total = points[-1]
    result.ok = True
    result.data = {
        "total_usd": latest_total,
        "peg_count": peg_count,
        "observed_on": latest_date,
        "history": points,
    }
    return result
