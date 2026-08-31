"""Command-line interface.

    sentinel run          fetch, evaluate, notify
    sentinel status       source health and data freshness
    sentinel report       re-render the most recent run
    sentinel history      week-over-week table for one series
    sentinel migrate      import the Perplexity export into the local mirror
    sentinel doctor       probe every endpoint and report what actually returns
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import load_config
from .migrate import migrate as run_migrate
from .pipeline import in_window
from .pipeline import run as run_pipeline
from .store import Store


def _store(args) -> Store:
    cfg = load_config(args.config, data_dir=args.data_dir)
    return Store(cfg.db_path)


def cmd_run(args) -> int:
    cfg = load_config(args.config, data_dir=args.data_dir)
    with Store(cfg.db_path) as store:
        result = run_pipeline(
            cfg,
            store=store,
            force=args.force,
            notify_force=args.notify_force,
            dry_run=args.dry_run,
        )
    if not result.ran:
        print(json.dumps({"ran": False, "reason": result.reason}))
        return 0
    if args.json:
        print(json.dumps(result.payload, indent=2, default=str))
    elif "console" not in cfg.notify.sinks or not result.notified:
        # The console sink already printed the body when a notification fired.
        print(result.text)
    if not args.json:
        print(f"\nnotification: {'sent' if result.notified else 'suppressed'} "
              f"({result.notify_reason})")
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config, data_dir=args.data_dir)
    with Store(cfg.db_path) as store:
        health = store.health()
        last = store.last_run()
        series = store.known_series()
        payload = {
            "database": str(cfg.db_path),
            "observations": store.observation_count(),
            "series_tracked": len(series),
            "last_run_utc": (last or {}).get("run_utc"),
            "last_asof": (last or {}).get("asof_date"),
            "regime": {
                "score": (last or {}).get("regime_score"),
                "label": (last or {}).get("regime_label"),
                "confidence": (last or {}).get("regime_confidence"),
            },
            "stale_series": (last or {}).get("stale_series", []),
            "source_health": health,
            "in_schedule_window": in_window(cfg),
        }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"database        {payload['database']}")
    print(f"observations    {payload['observations']:,} across {payload['series_tracked']} series")
    print(f"last run        {payload['last_run_utc'] or 'never'}")
    print(f"data as-of      {payload['last_asof'] or 'n/a'}")
    print(f"regime          {payload['regime']['label'] or 'n/a'} "
          f"({payload['regime']['score']}, {payload['regime']['confidence'] or 'n/a'} confidence)")
    stale = payload["stale_series"]
    print(f"stale series    {', '.join(stale) if stale else 'none'}")
    print("source health")
    for entry in health:
        failures = entry["consecutive_failures"]
        state = "ok" if not failures else f"{failures} fail(s)"
        print(f"  {entry['source']:<14} {state:<12} last ok: {entry['last_success'] or 'never'}")
        if entry["last_error"]:
            print(f"    {entry['last_error']}")
    return 0


def cmd_report(args) -> int:
    cfg = load_config(args.config, data_dir=args.data_dir)
    with Store(cfg.db_path) as store:
        last = store.last_run()
    if not last:
        print("no runs recorded yet; run `sentinel run` first", file=sys.stderr)
        return 1
    print(last.get("markdown") if args.markdown else json.dumps(last, indent=2, default=str))
    return 0


def cmd_history(args) -> int:
    cfg = load_config(args.config, data_dir=args.data_dir)
    with Store(cfg.db_path) as store:
        points = store.series_history(args.series, limit=args.limit)
    if not points:
        print(f"no observations for {args.series}", file=sys.stderr)
        return 1
    print(f"{'date':<12} {'value':>16} {'change':>14}")
    previous = None
    for obs_date, value in points:
        change = f"{value - previous:+,.3f}" if previous is not None else ""
        print(f"{obs_date.isoformat():<12} {value:>16,.3f} {change:>14}")
        previous = value
    return 0


def cmd_migrate(args) -> int:
    cfg = load_config(args.config, data_dir=args.data_dir)
    with Store(cfg.db_path) as store:
        summary = run_migrate(store)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_doctor(args) -> int:
    """Probe each endpoint and report what it actually returns.

    Written because the original failure was invisible: the multi-series FRED URL
    returned HTTP 200 and a ZIP archive, and nothing in the pipeline said so.
    """
    from .http import FetchError, fetch
    from .sources.fred import ALL_SERIES, CSV_ENDPOINT

    cfg = load_config(args.config, data_dir=args.data_dir)
    year = datetime.now(UTC).year
    checks: list[tuple[str, str]] = [
        ("stablecoins", cfg.stablecoins.url),
        ("ecb", cfg.ecb.url.format(year=year)),
    ]
    checks += [(f"fred:{s}", CSV_ENDPOINT.format(series=s)) for s in ALL_SERIES]
    checks.append(
        (
            "fred:multi-series (legacy path)",
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(ALL_SERIES[:5]),
        )
    )

    failures = 0
    for name, url in checks:
        try:
            response = fetch(url, cfg.http)
        except FetchError as exc:
            failures += 1
            print(f"  FAIL  {name:<34} {exc.last_error}")
            continue
        kind = "ZIP archive" if response.is_zip else "text"
        flag = "WARN" if response.is_zip else "ok  "
        print(
            f"  {flag}  {name:<34} {len(response.body):>8,}B  {kind}  "
            f"{response.elapsed_seconds:.2f}s  attempt {response.attempts}"
        )
    print(f"\n{len(checks) - failures}/{len(checks)} endpoints reachable")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel", description="Global liquidity monitor")
    parser.add_argument("--config", type=Path, default=None, help="path to sentinel.toml")
    parser.add_argument("--data-dir", type=Path, default=None, help="override the data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="fetch sources, evaluate thresholds, notify")
    run_p.add_argument("--force", action="store_true", help="ignore the schedule window")
    run_p.add_argument("--notify-force", action="store_true", help="bypass notification dedupe")
    run_p.add_argument(
        "--dry-run", action="store_true", help="evaluate without notifying or saving"
    )
    run_p.add_argument("--json", action="store_true")
    run_p.set_defaults(func=cmd_run)

    status_p = sub.add_parser("status", help="source health and data freshness")
    status_p.add_argument("--json", action="store_true")
    status_p.set_defaults(func=cmd_status)

    report_p = sub.add_parser("report", help="re-render the most recent run")
    report_p.add_argument("--markdown", action="store_true")
    report_p.set_defaults(func=cmd_report)

    hist_p = sub.add_parser("history", help="observation history for one series")
    hist_p.add_argument("series")
    hist_p.add_argument("--limit", type=int, default=20)
    hist_p.set_defaults(func=cmd_history)

    mig_p = sub.add_parser("migrate", help="import the Perplexity export")
    mig_p.set_defaults(func=cmd_migrate)

    doc_p = sub.add_parser("doctor", help="probe every endpoint")
    doc_p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
