"""DefiLlama aggregate stablecoin supply.

This is the one source that has never failed, so it carries the weekly signal on
its own when FRED is degraded.

The payload nests circulating supply under ``peggedAssets[].circulating`` as a
dict keyed by peg type (``peggedUSD``, ``peggedEUR``, ...). The original parser
walked a fixed key list and silently produced ``None`` when the shape shifted,
which is what ``stablecoins_note.json`` in the export recorded. This version
walks the nested structure explicitly and reports how many assets it summed so a
schema change shows up as a count drop rather than as silence.
"""

from __future__ import annotations

import json
import math

from ..config import Config
from ..http import FetchError, fetch
from .base import SourceResult

CIRCULATING_KEYS = ("circulating", "mcap", "marketCap", "market_cap")
PEG_VALUE_KEYS = ("peggedUSD", "peggedVAR", "peggedEUR", "usd", "value")


def _as_usd(node: object) -> float | None:
    """Coerce a DefiLlama circulating/mcap node into a USD float."""
    if isinstance(node, (int, float)) and math.isfinite(float(node)):
        return float(node)
    if isinstance(node, dict):
        for key in PEG_VALUE_KEYS:
            value = node.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
        # Unknown peg key: fall back to the single numeric member if unambiguous.
        numeric = [float(v) for v in node.values() if isinstance(v, (int, float))]
        if len(numeric) == 1 and math.isfinite(numeric[0]):
            return numeric[0]
    return None


def parse_payload(payload: object) -> tuple[float | None, int, list[str]]:
    """Return ``(total_usd, asset_count, notes)``."""
    notes: list[str] = []
    assets = payload.get("peggedAssets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        keys = sorted(payload.keys())[:10] if isinstance(payload, dict) else []
        notes.append(f"peggedAssets missing; top-level keys: {keys}")
        return None, 0, notes

    total = 0.0
    counted = 0
    skipped: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        value = None
        for key in CIRCULATING_KEYS:
            value = _as_usd(asset.get(key))
            if value is not None:
                break
        if value is None:
            skipped.append(str(asset.get("symbol") or asset.get("name") or "?"))
            continue
        total += value
        counted += 1

    if skipped:
        notes.append(f"{len(skipped)} asset(s) without a readable supply: {skipped[:5]}")
    if not counted:
        return None, 0, notes
    return total, counted, notes


def fetch_stablecoins(cfg: Config, *, fetcher=fetch) -> SourceResult:
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

    total, count, notes = parse_payload(payload)
    result.notes.extend(notes)
    if total is None:
        result.errors["parse"] = "no stablecoin supply values found"
        return result

    result.ok = True
    result.data = {"total_usd": total, "asset_count": count}
    return result
