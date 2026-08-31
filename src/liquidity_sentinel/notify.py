"""Alert delivery.

Deduplication matters more than the transport: the previous design could
re-notify on the same frozen reading week after week. A notification is
suppressed when the same set of alert keys already fired within
``notify.dedupe_days``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .alerts import Alert
from .config import Config
from .http import post_json


@dataclass
class NotifyResult:
    sent: bool
    reason: str
    delivered: list[str]


def alert_fingerprint(alerts: list[Alert]) -> str:
    return "|".join(sorted(a.key for a in alerts))


def should_notify(
    alerts: list[Alert],
    *,
    last_notification: dict | None,
    dedupe_days: int,
    now: datetime,
    force: bool = False,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if not alerts:
        return False, "no threshold events"
    if not last_notification:
        return True, "first notification"

    previous_alerts = last_notification.get("alerts", [])
    previous_keys = {a.get("key") for a in previous_alerts if isinstance(a, dict)}
    current_keys = {a.key for a in alerts}
    if current_keys - previous_keys:
        return True, "new alert type"

    raw = last_notification.get("run_utc")
    if not raw:
        return True, "no prior timestamp"
    try:
        previous = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True, "unparsable prior timestamp"
    if now - previous >= timedelta(days=dedupe_days):
        return True, "dedupe window elapsed"
    return False, f"duplicate alert set within {dedupe_days}d"


def _console(body: str) -> None:
    print(body)


def _file(body: str, payload: dict, cfg: Config) -> Path:
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = payload["run_utc"].replace(":", "").replace("-", "")
    path = cfg.runs_dir / f"alert_{stamp}.txt"
    path.write_text(body, encoding="utf-8")
    (cfg.runs_dir / f"run_{stamp}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return path


def deliver(
    body: str, payload: dict, alerts: list[Alert], cfg: Config
) -> list[str]:
    delivered: list[str] = []
    for sink in cfg.notify.sinks:
        try:
            if sink == "console":
                _console(body)
                delivered.append("console")
            elif sink == "file":
                path = _file(body, payload, cfg)
                delivered.append(f"file:{path}")
            elif sink == "webhook":
                if not cfg.notify.webhook_url:
                    continue
                status = post_json(cfg.notify.webhook_url, {"text": body}, cfg.http)
                delivered.append(f"webhook:{status}")
        except Exception as exc:  # noqa: BLE001 - a failed sink must not fail the run
            delivered.append(f"{sink}:error:{type(exc).__name__}")
    return delivered


def notify(
    body: str,
    payload: dict,
    alerts: list[Alert],
    cfg: Config,
    *,
    last_notification: dict | None,
    force: bool = False,
    now: datetime | None = None,
) -> NotifyResult:
    now = now or datetime.now(UTC)
    send, reason = should_notify(
        alerts,
        last_notification=last_notification,
        dedupe_days=cfg.notify.dedupe_days,
        now=now,
        force=force,
    )
    if not send:
        return NotifyResult(False, reason, [])
    return NotifyResult(True, reason, deliver(body, payload, alerts, cfg))
