# Legacy export

The original Perplexity scheduled task ("Global Liquidity Tracker", task id
`ecb64bd3`), kept verbatim for reference. Nothing here is executed by the
current project.

| File | Notes |
| --- | --- |
| `run_global_liquidity_tracker.py` | The original runner. Its FRED path is the subject of [`../docs/ROOT_CAUSE.md`](../docs/ROOT_CAUSE.md). |
| `record_20260828.py` | One-off script that hand-wrote the final run's state. |
| `global_liquidity_tracker_state.json` | Last persisted state. `latest.asof_date` is frozen at `2026-07-09`. |
| `fred_raw.bin` | The ZIP archive FRED returned for a multi-series request — the evidence for the root cause. |
| `fred_extracted.csv`, `fred_last_available.json` | Extraction artefacts from the last successful pull. |
| `findings_2026-08-14.json`, `findings_2026-08-28.json` | Per-run findings. `migrate` reads the stablecoin totals from these. |
| `stablecoins_raw.json` | Cached DefiLlama payload; the source of the test fixture. |
| `stablecoins_note.json` | Records the parser's schema mismatch, now covered by a test. |
| `ecb_wfs_index.html`, `ecb_latest.json` | Cached ECB index and the nav-chrome text the old extractor mistook for a headline. |
| `run_summary.json`, `last_run_note.json`, `last_pull_raw.json`, `current_run_output.json` | Final run outputs. |
| `PERPLEXITY_README.md` | The export's own README. |
| `task_config_snapshot.json` | Export-time task configuration. |

The wide FRED history that shipped as `fred_panel.csv` now lives at
[`../data/seed/fred_history.csv`](../data/seed/fred_history.csv) and is loaded by
`sentinel migrate`.
