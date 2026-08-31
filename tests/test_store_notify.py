"""Durable mirror and notification dedupe."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from liquidity_sentinel.alerts import Alert
from liquidity_sentinel.notify import should_notify

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_observations_round_trip(store):
    points = [(date(2026, 8, 19), 1.5), (date(2026, 8, 26), 2.5)]
    assert store.upsert_observations("SOFR", points, "fred_csv") == 2
    assert store.series_history("SOFR") == points


def test_revisions_overwrite_rather_than_duplicate(store):
    """FRED revises history; a re-fetch must update, not append."""
    store.upsert_observations("WALCL", [(date(2026, 8, 26), 100.0)], "fred_csv")
    store.upsert_observations("WALCL", [(date(2026, 8, 26), 101.0)], "fred_api")
    assert store.series_history("WALCL") == [(date(2026, 8, 26), 101.0)]
    assert store.observation_count() == 1


def test_history_returns_oldest_first_within_the_limit(store):
    points = [(date(2026, 8, d), float(d)) for d in range(1, 21)]
    store.upsert_observations("DGS10", points, "fred_csv")
    recent = store.series_history("DGS10", limit=5)
    assert recent == points[-5:]


def test_source_health_tracks_consecutive_failures(store):
    store.mark_failure("fred", "TimeoutError")
    store.mark_failure("fred", "TimeoutError")
    health = {h["source"]: h for h in store.health()}
    assert health["fred"]["consecutive_failures"] == 2

    store.mark_success("fred")
    health = {h["source"]: h for h in store.health()}
    assert health["fred"]["consecutive_failures"] == 0
    assert health["fred"]["last_error"] is None


def test_last_notification_skips_unnotified_runs(store):
    store.record_run({"run_utc": "2026-08-01T00:00:00Z", "alerts": [{"key": "a"}]}, notified=True)
    store.record_run({"run_utc": "2026-08-08T00:00:00Z", "alerts": []}, notified=False)
    assert store.last_run()["run_utc"] == "2026-08-08T00:00:00Z"
    assert store.last_notification()["run_utc"] == "2026-08-01T00:00:00Z"


def alert(key: str) -> Alert:
    return Alert(key, key, 1.0, 0.5)


def test_no_alerts_means_no_notification():
    assert should_notify([], last_notification=None, dedupe_days=6, now=NOW) == (
        False, "no threshold events"
    )


def test_first_alert_always_notifies():
    send, reason = should_notify([alert("a")], last_notification=None, dedupe_days=6, now=NOW)
    assert send and reason == "first notification"


def test_repeat_of_the_same_alert_set_is_suppressed():
    """Guards against a frozen reading re-notifying week after week."""
    previous = {"run_utc": (NOW - timedelta(days=2)).isoformat(), "alerts": [{"key": "a"}]}
    send, reason = should_notify([alert("a")], last_notification=previous, dedupe_days=6, now=NOW)
    assert not send
    assert "duplicate" in reason


def test_a_new_alert_type_breaks_through_dedupe():
    previous = {"run_utc": (NOW - timedelta(days=1)).isoformat(), "alerts": [{"key": "a"}]}
    send, reason = should_notify(
        [alert("a"), alert("b")], last_notification=previous, dedupe_days=6, now=NOW
    )
    assert send and reason == "new alert type"


def test_dedupe_expires():
    previous = {"run_utc": (NOW - timedelta(days=7)).isoformat(), "alerts": [{"key": "a"}]}
    send, reason = should_notify([alert("a")], last_notification=previous, dedupe_days=6, now=NOW)
    assert send and reason == "dedupe window elapsed"


def test_force_overrides_everything():
    send, reason = should_notify([], last_notification=None, dedupe_days=6, now=NOW, force=True)
    assert send and reason == "forced"


def test_unparsable_prior_timestamp_fails_open():
    previous = {"run_utc": "not-a-date", "alerts": [{"key": "a"}]}
    send, _ = should_notify([alert("a")], last_notification=previous, dedupe_days=6, now=NOW)
    assert send, "a corrupt record must not silence alerts"
