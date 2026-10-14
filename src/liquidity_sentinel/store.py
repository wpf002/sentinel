"""Durable local mirror of every observation the tracker has ever seen.

The original tracker kept only the latest snapshot in a JSON blob, so one failed
fetch left it with nothing to fall back on beyond that single frozen snapshot.
Here every observation is appended to SQLite, which makes three things possible:

* a failed fetch degrades to the newest cached value instead of to nothing;
* staleness is measured per series rather than for FRED as a whole;
* week-over-week comparisons use the real prior observation, not "whatever the
  previous successful run happened to store".
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    series     TEXT NOT NULL,
    obs_date   TEXT NOT NULL,
    value      REAL NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series, obs_date)
);
CREATE INDEX IF NOT EXISTS idx_obs_series_date ON observations (series, obs_date DESC);

CREATE TABLE IF NOT EXISTS runs (
    run_utc      TEXT PRIMARY KEY,
    asof_date    TEXT,
    regime_score INTEGER,
    regime_label TEXT,
    alerts_json  TEXT NOT NULL DEFAULT '[]',
    notified     INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
    source        TEXT PRIMARY KEY,
    last_success  TEXT,
    last_failure  TEXT,
    last_error    TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Store:
    """Thin SQLite wrapper. Use as a context manager."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- observations ----------------------------------------------------

    def upsert_observations(
        self, series: str, points: Iterable[tuple[date, float]], source: str
    ) -> int:
        """Insert or refresh observations. Returns the number of rows written.

        FRED revises history, so an existing (series, date) is overwritten rather
        than ignored.
        """
        stamp = _utcnow()
        rows = [(series, d.isoformat(), float(v), source, stamp) for d, v in points]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO observations (series, obs_date, value, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(series, obs_date) DO UPDATE SET "
            "value=excluded.value, source=excluded.source, fetched_at=excluded.fetched_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def series_history(self, series: str, limit: int = 400) -> list[tuple[date, float]]:
        """Most recent *limit* observations, oldest first."""
        cur = self.conn.execute(
            "SELECT obs_date, value FROM observations WHERE series = ? "
            "ORDER BY obs_date DESC LIMIT ?",
            (series, limit),
        )
        rows = [(date.fromisoformat(r["obs_date"]), float(r["value"])) for r in cur]
        rows.reverse()
        return rows

    def latest_observation_dates(self) -> dict[str, date]:
        """Newest stored observation date per series, in one query."""
        cur = self.conn.execute(
            "SELECT series, MAX(obs_date) AS obs_date FROM observations GROUP BY series"
        )
        return {r["series"]: date.fromisoformat(r["obs_date"]) for r in cur}

    def known_series(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT series FROM observations ORDER BY series")
        return [r["series"] for r in cur]

    def observation_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    # -- runs ------------------------------------------------------------

    def record_run(self, payload: dict, *, notified: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_utc, asof_date, regime_score, regime_label, alerts_json, notified, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload["run_utc"],
                payload.get("asof_date"),
                payload.get("regime_score"),
                payload.get("regime_label"),
                json.dumps(payload.get("alerts", [])),
                1 if notified else 0,
                json.dumps(payload, default=str),
            ),
        )
        self.conn.commit()

    def recent_runs(self, limit: int = 10) -> list[dict]:
        cur = self.conn.execute(
            "SELECT payload_json, notified FROM runs ORDER BY run_utc DESC LIMIT ?", (limit,)
        )
        out = []
        for row in cur:
            payload = json.loads(row["payload_json"])
            payload["notified"] = bool(row["notified"])
            out.append(payload)
        return out

    def last_run(self) -> dict | None:
        runs = self.recent_runs(1)
        return runs[0] if runs else None

    def last_notification(self) -> dict | None:
        cur = self.conn.execute(
            "SELECT payload_json FROM runs WHERE notified = 1 ORDER BY run_utc DESC LIMIT 1"
        )
        row = cur.fetchone()
        return json.loads(row["payload_json"]) if row else None

    # -- source health ---------------------------------------------------

    def mark_success(self, source: str) -> None:
        self.conn.execute(
            "INSERT INTO source_health (source, last_success, consecutive_failures) "
            "VALUES (?, ?, 0) "
            "ON CONFLICT(source) DO UPDATE SET last_success=excluded.last_success, "
            "consecutive_failures=0, last_error=NULL",
            (source, _utcnow()),
        )
        self.conn.commit()

    def mark_failure(self, source: str, error: str) -> None:
        self.conn.execute(
            "INSERT INTO source_health (source, last_failure, last_error, consecutive_failures) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(source) DO UPDATE SET last_failure=excluded.last_failure, "
            "last_error=excluded.last_error, "
            "consecutive_failures=source_health.consecutive_failures + 1",
            (source, _utcnow(), error[:500]),
        )
        self.conn.commit()

    def health(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM source_health ORDER BY source")
        return [dict(r) for r in cur]

    # -- meta ------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_json(self, key: str) -> dict | None:
        raw = self.get_meta(key)
        return json.loads(raw) if raw else None

    def set_json(self, key: str, value: dict | Sequence) -> None:
        self.set_meta(key, json.dumps(value, default=str))
