"""Human-readable run output.

``render_text`` is what lands in a terminal or a notification body;
``render_markdown`` is what gets written to ``data/runs/`` for the record.
Both lead with what changed, then data freshness, because a run whose sources
degraded should say so before it shows numbers.
"""

from __future__ import annotations

from .alerts import Alert
from .metrics import Snapshot
from .signals import Regime

ARROW = {"expanding": "▲", "contracting": "▼", "neutral": "→"}


def _line(label: str, value: str) -> str:
    return f"  {label:<26} {value}"


def _fmt(value: float | None, spec: str, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return format(value, spec) + suffix


def summary_line(regime: Regime, alerts: list[Alert]) -> str:
    mark = ARROW.get(regime.label, "→")
    head = f"{mark} {regime.label.upper()} ({regime.score:+d}/{regime.available} signals)"
    if alerts:
        return f"{head} — {len(alerts)} alert(s)"
    return f"{head} — no threshold events"


def render_text(
    snapshot: Snapshot,
    regime: Regime,
    alerts: list[Alert],
    *,
    run_utc: str,
    source_status: dict[str, str],
) -> str:
    out: list[str] = []
    out.append(f"Liquidity Sentinel — {run_utc}")
    out.append(summary_line(regime, alerts))
    out.append("")

    if alerts:
        out.append("Alerts")
        for alert in alerts:
            out.append(f"  [{alert.severity}] {alert.message}")
        out.append("")

    out.append("Liquidity")
    out.append(_line("Fed net liquidity", _fmt(snapshot.get("fed_net_liquidity_bil"), ",.1f", "B")))
    out.append(_line("  WoW", _fmt(snapshot.get("fed_net_liquidity_wow_bil"), "+,.1f", "B")))
    out.append(_line("Balance sheet (WALCL)", _fmt(snapshot.get("WALCL_bil"), ",.1f", "B")))
    out.append(_line("Reserves (WRESBAL)", _fmt(snapshot.get("WRESBAL_bil"), ",.1f", "B")))
    out.append(_line("TGA (WTREGEN)", _fmt(snapshot.get("WTREGEN_bil"), ",.1f", "B")))
    out.append(_line("Reverse repo (RRP)", _fmt(snapshot.get("RRPONTSYD_bil"), ",.3f", "B")))
    out.append(_line("M2", _fmt(snapshot.get("M2_bil"), ",.1f", "B")))
    out.append("")

    out.append("Stablecoins")
    total = snapshot.get("stablecoins_total_usd")
    out.append(_line("Aggregate supply", _fmt(total / 1e9 if total else None, ",.2f", "B")))
    wow = snapshot.get("stablecoins_wow_usd")
    out.append(_line("  WoW", _fmt(wow / 1e9 if wow is not None else None, "+,.2f", "B")))
    pct = snapshot.get("stablecoins_wow_pct")
    out.append(_line("  WoW %", _fmt(pct * 100 if pct is not None else None, "+,.2f", "%")))
    count = snapshot.get("stablecoins_peg_count")
    out.append(_line("Peg currencies", _fmt(count, ",.0f")))
    span = snapshot.get("stablecoins_change_days")
    if span is not None and span != 7:
        out.append(_line("Comparison window", f"{span:.0f} days"))
    out.append("")

    out.append("Credit & conditions")
    out.append(_line("IG spread", _fmt(snapshot.get("BAMLC0A0CM_pct"), ".2f", "%")))
    out.append(_line("HY spread", _fmt(snapshot.get("BAMLH0A0HYM2_pct"), ".2f", "%")))
    out.append(_line("NFCI", _fmt(snapshot.get("NFCI"), "+.3f")))
    out.append(_line("ANFCI", _fmt(snapshot.get("ANFCI"), "+.3f")))
    out.append("")

    out.append("Rates & funding")
    out.append(_line("2Y / 10Y", f"{_fmt(snapshot.get('DGS2_pct'), '.2f')} / "
                                 f"{_fmt(snapshot.get('DGS10_pct'), '.2f')}"))
    out.append(_line("10Y-2Y", _fmt(snapshot.get("T10Y2Y_pct"), "+.2f")))
    out.append(_line("SOFR - IORB", _fmt(snapshot.get("SOFR_minus_IORB_bps"), "+.0f", " bps")))
    out.append(_line("SOFR - EFFR", _fmt(snapshot.get("SOFR_minus_EFFR_bps"), "+.0f", " bps")))
    out.append("")

    out.append("Data freshness")
    out.append(_line("As-of", snapshot.asof_date or "n/a"))
    out.append(_line("Prior week", snapshot.asof_prior_date or "n/a"))
    out.append(_line("Fresh series", str(len(snapshot.fresh_series))))
    if snapshot.stale_series:
        out.append(_line("Stale series", ", ".join(sorted(snapshot.stale_series))))
    for name, status in sorted(source_status.items()):
        out.append(_line(f"Source: {name}", status))
    if snapshot.notes:
        out.append("")
        out.append("Notes")
        for note in snapshot.notes:
            out.append(f"  - {note}")
    return "\n".join(out)


def render_markdown(
    snapshot: Snapshot,
    regime: Regime,
    alerts: list[Alert],
    *,
    run_utc: str,
    source_status: dict[str, str],
) -> str:
    out = [f"# Liquidity Sentinel — {run_utc}", "", f"**{summary_line(regime, alerts)}**", ""]
    out.append(f"Signals: {', '.join(regime.notes) or 'none available'}")
    out.append(f"Confidence: {regime.confidence} ({regime.available} inputs)")
    out.append("")
    if alerts:
        out += ["## Alerts", "", "| Severity | Alert |", "| --- | --- |"]
        out += [f"| {a.severity} | {a.message} |" for a in alerts]
        out.append("")
    out += ["## Readings", "", "| Metric | Value | WoW |", "| --- | --- | --- |"]
    rows = [
        ("Fed net liquidity", "fed_net_liquidity_bil", "fed_net_liquidity_wow_bil", ",.1f", "B"),
        ("WALCL", "WALCL_bil", "WALCL_wow_bil", ",.1f", "B"),
        ("Reserves", "WRESBAL_bil", "WRESBAL_wow_bil", ",.1f", "B"),
        ("TGA", "WTREGEN_bil", "WTREGEN_wow_bil", ",.1f", "B"),
        ("RRP", "RRPONTSYD_bil", "RRPONTSYD_wow_bil", ",.3f", "B"),
        ("M2", "M2_bil", "M2_wow_bil", ",.1f", "B"),
    ]
    for label, value_key, change_key, spec, suffix in rows:
        out.append(
            f"| {label} | {_fmt(snapshot.get(value_key), spec, suffix)} "
            f"| {_fmt(snapshot.get(change_key), '+' + spec, suffix)} |"
        )
    total = snapshot.get("stablecoins_total_usd")
    change = snapshot.get("stablecoins_wow_usd")
    out.append(
        f"| Stablecoin supply | {_fmt(total / 1e9 if total else None, ',.2f', 'B')} "
        f"| {_fmt(change / 1e9 if change is not None else None, '+,.2f', 'B')} |"
    )
    out += ["", "## Freshness", "", f"- As-of: {snapshot.asof_date or 'n/a'}"]
    out.append(f"- Fresh series: {len(snapshot.fresh_series)}")
    out.append(f"- Stale series: {', '.join(sorted(snapshot.stale_series)) or 'none'}")
    for name, status in sorted(source_status.items()):
        out.append(f"- {name}: {status}")
    return "\n".join(out)
