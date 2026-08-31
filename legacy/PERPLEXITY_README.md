# Liquidity Sentinel

Liquidity Sentinel is a credit-efficient Perplexity recurring monitor for global liquidity signals.

## Current product status

This is a useful MVP monitor, not a fully finished product. The reason is FRED reliability: recent scheduled runs repeatedly timed out when fetching FRED CSV data, so the tracker now treats FRED readings as stale context unless a fresh FRED pull succeeds.

The project is currently optimized to spend as few Perplexity credits as practical:

- Runs once weekly only.
- Uses direct endpoints only.
- Avoids broad web research, browser automation, deep analysis, optional browsing, and repeated retries.
- Keeps a saved state file for comparisons.
- Sends an in-app notification only for a material stablecoin move, FRED recovery, first complete baseline, or a fresh-FRED threshold/regime event.

## Active Perplexity scheduled task

- Current task name: Global Liquidity Tracker
- Project/export name: Liquidity Sentinel
- Task ID: ecb64bd3
- Task thread: https://www.perplexity.ai/computer/tasks/e660a414-ba3e-4fbc-8f82-f0bd8c34dc02
- Status at export time: active
- Schedule: Fridays at 12:00 UTC
- Central Time behavior: 7:00 AM Central during daylight saving time; 6:00 AM Central during standard time
- Next run at export time: Friday, September 04, 2026 at 7:00 AM CDT
- Last run at export time: Friday, August 28, 2026 at 7:01 AM CDT
- Run count at export time: 6

## Current operating model

### Weekly always-on signal

- DefiLlama stablecoin supply: https://stablecoins.llama.fi/stablecoins?includePrices=true

This is the reliable fresh weekly signal. The tracker monitors aggregate stablecoin supply and week-over-week dollar/percentage change.

### FRED retry policy

FRED has repeatedly timed out, so the tracker no longer retries all FRED groups every week.

- If FRED failures remain elevated or the last complete FRED snapshot is more than two weeks stale, FRED is attempted only on the first Friday of each month or when a prior run records recovery.
- When attempted, the tracker first pulls only the minimal core FRED group:
  - https://fred.stlouisfed.org/graph/fredgraph.csv?id=WALCL,WRESBAL,WTREGEN,RRPONTSYD,WM2NS
- Secondary FRED groups are pulled only if the core pull succeeds:
  - https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A0CM,BAMLH0A0HYM2,NFCI,ANFCI
  - https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2,DGS10,T10Y2Y,SOFR,EFFR,IORB

### ECB policy

The ECB weekly statement index is checked only when FRED core succeeds in the same run or on the first Friday of the month:

- https://www.ecb.europa.eu/press/pr/wfs/html/index.en.html

If extraction fails, the tracker skips it and does not search or browse.

## Core calculations

- Stablecoins: aggregate supply and week-over-week dollar/percentage change.
- Fed net liquidity, only when fresh FRED succeeds: WALCL - WTREGEN - RRPONTSYD.
- Fed components, only when fresh FRED succeeds: WALCL, WRESBAL, WTREGEN, RRPONTSYD.
- M2, only when fresh FRED succeeds: WM2NS.
- Optional secondary FRED metrics, only after core FRED succeeds: credit spreads, financial conditions, rates, and funding-stress spreads.

## Alert logic

The tracker sends a concise in-app notification only when one of these occurs:

- Stablecoin supply changes by at least 1.0% week over week or at least $2B.
- Fresh FRED core returns after being stale.
- A first complete baseline is created.
- A fresh-FRED threshold or regime event occurs.

It does not notify solely because a source failed.

## Included files

- `tracker_files/run_global_liquidity_tracker.py`: scheduled runner script from the task workspace.
- `tracker_files/global_liquidity_tracker_state.json`: persistent tracker state.
- `tracker_files/current_run_output.json`: latest captured run output.
- `tracker_files/findings_2026-08-14.json` and `tracker_files/findings_2026-08-28.json`: recent run findings.
- `tracker_files/fred_*`: cached FRED data and extraction artifacts.
- `tracker_files/stablecoins_*`: cached stablecoin artifacts.
- `tracker_files/ecb_*`: cached ECB artifacts.
- `task_config_snapshot.json`: export-time configuration summary.

## Finish-line recommendation

To make Liquidity Sentinel a finished product, replace the fragile FRED CSV path with a more reliable data layer, such as a keyed FRED API workflow, a durable cache/mirror, or an external lightweight fetcher that writes clean snapshots before the Perplexity scheduled task runs.
