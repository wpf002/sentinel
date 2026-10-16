"""The weekly run.

Order of operations, and why:

1. Fetch each source independently. No source can block another.
2. Write whatever landed into the SQLite mirror.
3. Build the snapshot **from the mirror**, not from the fetch result. A series
   that failed this week still contributes its last known value, flagged with its
   age, instead of vanishing.
4. Score, alert, notify, record.

Step 3 is the structural fix: the run is no longer all-or-nothing on FRED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from . import alerts as alert_rules
from .config import Config
from .http import fetch
from .metrics import Snapshot, apply_stablecoins, build_snapshot
from .notify import notify
from .report import render_markdown, render_text
from .signals import Regime, score_regime
from .sources import fetch_ecb, fetch_fred, fetch_stablecoins
from .sources.fred import ALL_SERIES
from .store import Store

# The aggregate is stored as a series so it shares the seven-day change rule.
STABLECOIN_SERIES = "STABLECOINS_TOTAL"


def utc_stamp(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(UTC)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def in_window(cfg: Config, moment: datetime | None = None) -> bool:
    """True when *moment* falls inside the configured schedule window."""
    if not cfg.run.weekdays:
        return True
    local = (moment or datetime.now(UTC)).astimezone(ZoneInfo(cfg.run.timezone))
    return local.isoweekday() in cfg.run.weekdays and local.hour == cfg.run.hour


def _merge_fetched(
    cached: dict[str, list[tuple[date, float]]],
    fetched: dict[str, list[tuple[date, float]]],
) -> dict[str, list[tuple[date, float]]]:
    """Overlay freshly fetched points on cached history without writing to disk.

    Fetched values win on a date collision, matching what ``upsert_observations``
    would have done.
    """
    merged = {k: list(v) for k, v in cached.items()}
    for series, points in fetched.items():
        combined = dict(merged.get(series, []))
        combined.update(dict(points))
        merged[series] = sorted(combined.items())
    return merged


@dataclass
class RunResult:
    ran: bool
    reason: str = ""
    run_utc: str = ""
    snapshot: Snapshot | None = None
    regime: Regime | None = None
    alerts: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    text: str = ""
    notified: bool = False
    notify_reason: str = ""


def _source_status(result) -> str:
    if result.ok:
        return "ok"
    if result.data:
        return f"partial ({result.error_summary})"
    return f"failed ({result.error_summary or 'unknown'})"


def run(
    cfg: Config,
    *,
    store: Store,
    force: bool = False,
    notify_force: bool = False,
    dry_run: bool = False,
    fetcher=fetch,
    today: date | None = None,
    now: datetime | None = None,
) -> RunResult:
    now = now or datetime.now(UTC)
    today = today or now.date()

    if cfg.run.enforce_window and not force and not in_window(cfg, now):
        return RunResult(False, reason="outside scheduled window", run_utc=utc_stamp(now))

    run_utc = utc_stamp(now)
    source_status: dict[str, str] = {}

    # 1-2. Fetch, then persist unless this is a dry run. A dry run must leave the
    # mirror, the source-health table and the run log exactly as it found them,
    # so it merges what it fetched over the cached history in memory instead.
    persist = not dry_run

    fred = fetch_fred(cfg, fetcher=fetcher)
    if persist:
        for series, points in (fred.data or {}).items():
            store.upsert_observations(series, points, fred.labels.get(series, "fred"))
        if fred.data:
            store.mark_success("fred")
        if fred.errors:
            store.mark_failure("fred", fred.error_summary or "unknown")
    source_status["fred"] = (
        f"{len(fred.data or {})}/{len(ALL_SERIES)} series"
        + (f" — {len(fred.errors)} failed" if fred.errors else "")
    )

    stable = fetch_stablecoins(cfg, fetcher=fetcher)
    if persist:
        if stable.ok:
            store.mark_success("stablecoins")
        else:
            store.mark_failure("stablecoins", stable.error_summary or "unknown")
    source_status["stablecoins"] = _source_status(stable)

    ecb = fetch_ecb(cfg, fetcher=fetcher)
    if persist:
        if ecb.ok:
            store.mark_success("ecb")
            store.set_json("ecb_latest", ecb.data)
        elif cfg.ecb.enabled:
            store.mark_failure("ecb", ecb.error_summary or "unknown")
    source_status["ecb"] = _source_status(ecb)

    # 3. Build from the durable mirror, so cached series still contribute.
    histories = {
        series: store.series_history(series)
        for series in ALL_SERIES
        if store.series_history(series, limit=1)
    }
    if not persist:
        histories = _merge_fetched(histories, fred.data or {})
    live = {s: fred.labels.get(s, "cache") for s in (fred.data or {})}
    snapshot = build_snapshot(
        histories, today=today, stale_after_days=cfg.fred.stale_days_for, sources=live
    )

    previous_run = store.last_run()

    # The aggregate is stored as an observation series so its week-over-week
    # change uses the same seven-day rule as every FRED series. The source
    # returns its full daily history, so each run refreshes the whole series
    # rather than appending one point.
    fetched_history = list(stable.data.get("history") or []) if stable.ok else []
    if fetched_history and persist:
        store.upsert_observations(STABLECOIN_SERIES, fetched_history, "defillama")
    history = store.series_history(STABLECOIN_SERIES)
    if fetched_history and not persist:
        history = _merge_fetched(
            {STABLECOIN_SERIES: history}, {STABLECOIN_SERIES: fetched_history}
        )[STABLECOIN_SERIES]

    if stable.ok:
        observed_on = stable.data.get("observed_on") or today
        apply_stablecoins(
            snapshot, stable.data["total_usd"], stable.data["peg_count"], history,
            observed_on=observed_on,
        )
        snapshot.dates["stablecoins_date"] = observed_on.isoformat()
    else:
        carried = history[-1][1] if history else None
        apply_stablecoins(
            snapshot, carried, None, history[:-1] if history else None, observed_on=today
        )
        if carried is not None:
            snapshot.notes.append("stablecoin supply carried forward; live pull failed")

    if ecb.ok:
        snapshot.dates["ecb_date"] = ecb.data["date"]
    if fred.errors:
        snapshot.notes.append(f"FRED series unavailable: {', '.join(sorted(fred.errors))}")

    # 4. Score, alert, notify, record.
    # Surfaced so `report` can flag reserves sitting below the floor. The floor
    # itself is a crossing alert, not a level alert, so the standing condition
    # needs somewhere to show up that is not the alert list.
    snapshot.values["reserves_floor_bil"] = cfg.alerts.get("reserves_floor_bil")

    regime = score_regime(snapshot)
    fired = alert_rules.evaluate(snapshot, cfg.alerts)
    fired += alert_rules.regime_alerts(regime, previous_run, cfg.alerts)

    text = render_text(
        snapshot, regime, fired, run_utc=run_utc, source_status=source_status
    )
    payload = {
        "run_utc": run_utc,
        "asof_date": snapshot.asof_date,
        "asof_prior_date": snapshot.asof_prior_date,
        "values": snapshot.values,
        "dates": snapshot.dates,
        "regime_score": regime.score,
        "regime_label": regime.label,
        "regime_confidence": regime.confidence,
        "signal_notes": regime.notes,
        "alerts": [a.as_dict() for a in fired],
        "fresh_series": snapshot.fresh_series,
        "stale_series": snapshot.stale_series,
        "source_status": source_status,
        "notes": snapshot.notes,
        "markdown": render_markdown(
            snapshot, regime, fired, run_utc=run_utc, source_status=source_status
        ),
    }

    result = RunResult(
        True, run_utc=run_utc, snapshot=snapshot, regime=regime, alerts=fired,
        payload=payload, text=text,
    )

    if dry_run:
        result.notify_reason = "dry run"
        return result

    outcome = notify(
        text, payload, fired, cfg,
        last_notification=store.last_notification(), force=notify_force, now=now,
    )
    result.notified = outcome.sent
    result.notify_reason = outcome.reason
    payload["notify_reason"] = outcome.reason
    payload["delivered"] = outcome.delivered
    store.record_run(payload, notified=outcome.sent)
    return result
