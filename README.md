# Liquidity Sentinel

A weekly global-liquidity monitor. It tracks Fed net liquidity and its
components, M2, credit spreads, financial conditions, rates and funding spreads,
and aggregate stablecoin supply — then scores the regime and alerts only when a
configured threshold is crossed.

This repository is the standalone successor to a Perplexity scheduled task. The
original runner and its data are preserved under [`legacy/`](legacy/).

## Why it was rebuilt

The exported tracker had been reporting the same frozen snapshot (`2026-07-09`)
for six consecutive weekly runs, attributing it to FRED timeouts. The timeouts
were real but secondary. The primary bug:

> `fredgraph.csv?id=WALCL,WRESBAL,WTREGEN,RRPONTSYD,WM2NS` does not return CSV.
> When the requested series span more than one observation frequency, FRED
> returns a **ZIP archive** containing one CSV per frequency.

The old runner passed those bytes to `csv.DictReader`, parsed zero rows, and
concluded FRED was unavailable — on every run, including the ones where the HTTP
request succeeded. The export's own `fred_raw.bin` is that ZIP. Full analysis in
[`docs/ROOT_CAUSE.md`](docs/ROOT_CAUSE.md); verify it yourself with:

```bash
python -m liquidity_sentinel doctor
```

Fetching one series at a time avoids the ZIP entirely, and drops each payload
from roughly 600 KB to 20 KB, which also resolves the timeouts.

## What changed

| Area | Before | Now |
| --- | --- | --- |
| FRED fetch | 3 multi-series URLs, ZIP-blind | 15 per-series pulls, ZIP-aware, optional keyed API |
| Retries | 1 blind retry per group | Exponential backoff with jitter, 4 attempts, no retry on hard 4xx |
| Failure blast radius | One failure ⇒ all FRED stale | Per-series; 14 good series still report |
| Persistence | Latest snapshot in one JSON blob | SQLite mirror of every observation ever seen |
| Week-over-week | vs. the last successful *run* | vs. the real prior *observation* |
| Stablecoin history | One point per run | 3,244 daily points from the source |
| Net liquidity | Mixed observation dates | Components aligned to the weekly anchor date |
| Staleness | Global boolean | Per-series age in days, gates the rules that use it |
| Alerts | Bare strings | Value, threshold, severity; deduped across runs |
| Regime score | Score only | Score plus input coverage and confidence |
| Config | Hardcoded in the script | `config/sentinel.toml` |
| Observability | None | `status`, `doctor`, `history`, source-health table |

Two smaller bugs surfaced during the rebuild: the ECB extractor was scraping
navigation chrome off a JS shell page rather than the release list, and Fed net
liquidity was mixing weekly and daily observation dates. Both are covered in the
root-cause document.

## Install

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

Runtime dependencies: none (standard library only). `pytest` and `ruff` are dev
extras.

## Quick start

```bash
python -m liquidity_sentinel migrate && python -m liquidity_sentinel run --force
```

`migrate` loads the exported FRED history (back to 1962) into the local mirror so
the first run has a real week-prior comparison instead of starting cold.

## Commands

| Command | Purpose |
| --- | --- |
| `sentinel run` | Fetch, evaluate thresholds, notify, record |
| `sentinel status` | Source health, freshness, last regime |
| `sentinel report --markdown` | Re-render the most recent run |
| `sentinel history WALCL` | Observation history for one series |
| `sentinel doctor` | Probe every endpoint; flags ZIP responses |
| `sentinel migrate` | Import the Perplexity export |

Useful flags: `--force` ignores the schedule window, `--dry-run` evaluates
without notifying or writing, `--json` emits the full payload.

## Configuration

Everything tunable lives in [`config/sentinel.toml`](config/sentinel.toml) —
thresholds, endpoints, retry policy, staleness limits, notification sinks. Code
contains no hardcoded thresholds.

Secrets come from the environment (see [`.env.example`](.env.example)):

- `FRED_API_KEY` — optional. Enables the keyed JSON API as the primary FRED
  path, with the keyless CSV endpoint as fallback. Everything works without it.
- `SENTINEL_WEBHOOK_URL` — optional. Slack-compatible `{"text": ...}` payload.

## Data sources

| Source | Endpoint | Role |
| --- | --- | --- |
| FRED | per-series `fredgraph.csv`, or the keyed API | Fed balance sheet, M2, spreads, rates |
| DefiLlama | `stablecoins.llama.fi/stablecoincharts/all` | Aggregate stablecoin supply, full daily history |
| ECB | weekly financial statement index | Publication date only; never gates a run |

## Metrics

- **Fed net liquidity** — `WALCL - WTREGEN - RRPONTSYD`, components sampled
  as-of the weekly anchor date rather than each series' own latest print.
- **Regime score** — one vote per available signal, clamped to ±5. `≥ +2`
  expanding, `≤ -2` contracting. Reported with input coverage, so a `+3` from
  nine signals is distinguishable from a `+3` from two.
- **Week-over-week** — computed against the newest observation at least seven
  days older than the latest one. Every source supplies its own dense history,
  so a gap between runs never stretches the comparison window. When history is
  sparse enough that it does, the run says so and reports the actual span.

## Layout

```
src/liquidity_sentinel/
  config.py      TOML + env configuration
  http.py        retry, backoff, ZIP detection
  store.py       SQLite observation mirror
  sources/       fred.py, stablecoins.py, ecb.py
  metrics.py     snapshot construction, unit handling, alignment
  signals.py     regime scoring
  alerts.py      threshold rules
  notify.py      delivery + dedupe
  report.py      text and markdown rendering
  pipeline.py    the weekly run
  migrate.py     Perplexity export import
legacy/          original runner, state, and cached artifacts
data/seed/       exported FRED history used by `migrate`
docs/            root-cause analysis
```

## Scheduling

The original task ran Fridays at 12:00 UTC. `config/sentinel.toml` keeps that
window, and `sentinel run` enforces it only when `run.enforce_window = true`;
`--force` bypasses it. Point any scheduler at:

```bash
cd /path/to/sentinel && .venv/bin/python -m liquidity_sentinel run
```

## Tests

```bash
pytest
```

Network is never touched in tests; sources are exercised against recorded
fixtures, including the real ZIP-response case.
