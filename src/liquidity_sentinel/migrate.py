"""Import the Perplexity export into the SQLite mirror.

The export's ``fred_panel.csv`` holds a wide multi-series history back to 1962.
Loading it means the first local run already has a real week-prior comparison
instead of starting cold, and it preserves the readings the scheduled task
collected before FRED broke.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

from .sources.fred import ALL_SERIES
from .store import Store

SEED_CSV = Path("data/seed/fred_history.csv")
LEGACY_STATE = Path("legacy/tracker_files/global_liquidity_tracker_state.json")
LEGACY_FINDINGS = Path("legacy/tracker_files")


def load_wide_csv(text: str) -> dict[str, list[tuple[date, float]]]:
    """Parse a wide ``DATE,SERIES_A,SERIES_B,...`` history into per-series points."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {}
    date_field = next(
        (f for f in reader.fieldnames if f.strip().lower() in {"date", "observation_date"}), None
    )
    if date_field is None:
        return {}
    columns = [f for f in reader.fieldnames if f != date_field]
    out: dict[str, list[tuple[date, float]]] = {c: [] for c in columns}

    for row in reader:
        raw_date = (row.get(date_field) or "").strip()
        if not raw_date:
            continue
        try:
            obs_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        for column in columns:
            raw = (row.get(column) or "").strip()
            if raw in {"", ".", "NaN", "nan"}:
                continue
            try:
                out[column].append((obs_date, float(raw)))
            except ValueError:
                continue
    return {k: sorted(v) for k, v in out.items() if v}


def import_seed(store: Store, path: Path = SEED_CSV) -> dict[str, int]:
    """Load the exported wide history. Returns ``{series: rows_written}``."""
    if not path.exists():
        return {}
    parsed = load_wide_csv(path.read_text(encoding="utf-8-sig"))
    written: dict[str, int] = {}
    for series, points in parsed.items():
        if series not in ALL_SERIES:
            continue
        written[series] = store.upsert_observations(series, points, "seed_export")
    return written


def import_legacy_state(store: Store, path: Path = LEGACY_STATE) -> dict | None:
    """Preserve the last Perplexity-era snapshot for reference and continuity."""
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    latest = state.get("latest") or {}
    store.set_json(
        "legacy_state",
        {
            "last_run_utc": state.get("last_run_utc"),
            "asof_date": latest.get("asof_date"),
            "stablecoins_total_usd": latest.get("stablecoins_total_usd"),
            "regime_score": latest.get("regime_score"),
            "regime_label": latest.get("regime_label"),
            "failures": state.get("failures"),
        },
    )
    return latest


def import_stablecoin_history(store: Store, directory: Path = LEGACY_FINDINGS) -> int:
    """Seed the stablecoin series from the export's per-run findings files.

    Each ``findings_YYYY-MM-DD.json`` records that run's aggregate, so the first
    local run has a genuine week-prior comparison instead of none.
    """
    if not directory.exists():
        return 0
    points: list[tuple[date, float]] = []
    for path in sorted(directory.glob("findings_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        total = payload.get("stablecoins_total_usd")
        raw_run = payload.get("run_utc") or ""
        if not isinstance(total, (int, float)) or not raw_run:
            continue
        try:
            observed = date.fromisoformat(raw_run[:10])
        except ValueError:
            continue
        points.append((observed, float(total)))
    if not points:
        return 0
    return store.upsert_observations("STABLECOINS_TOTAL", sorted(points), "seed_export")


def migrate(store: Store) -> dict:
    seeded = import_seed(store)
    legacy = import_legacy_state(store)
    stablecoin_rows = import_stablecoin_history(store)
    return {
        "series_seeded": len(seeded),
        "rows_seeded": sum(seeded.values()),
        "stablecoin_rows_seeded": stablecoin_rows,
        "legacy_asof": (legacy or {}).get("asof_date"),
        "total_observations": store.observation_count(),
    }
