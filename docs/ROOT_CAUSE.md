# Why the tracker was stuck

## Symptom

Six consecutive weekly runs (2026-07-17 through 2026-08-28) reported the same
FRED snapshot, dated `2026-07-09`. The run notes attributed it to timeouts:

```
"fred_error": "liquidity_m2: TimeoutError: The read operation timed out;
               spreads_conditions: TimeoutError: ...; rates_funding: TimeoutError: ..."
"failures": { "fred": 6 }
```

## Actual cause

The tracker requested several series in one URL:

```
https://fred.stlouisfed.org/graph/fredgraph.csv?id=WALCL,WRESBAL,WTREGEN,RRPONTSYD,WM2NS
```

When the requested ids span more than one observation frequency, that endpoint
does not return CSV. It returns a **ZIP archive** with one CSV per frequency:

```
$ unzip -l legacy/tracker_files/fred_raw.bin
     2950  README.txt
    23092  weekly,_as_of_wednesday.csv
    30343  weekly,_ending_wednesday.csv
   442827  daily.csv
    43412  weekly,_ending_monday.csv
    16668  daily,_close.csv
    70659  weekly,_ending_friday.csv
```

All three configured groups mixed frequencies, so all three returned archives.
`run_global_liquidity_tracker.py` decoded those bytes as UTF-8 and fed them to
`csv.DictReader`, which yielded no parsable rows. That set `fred_available` to
false, and the run fell back to the last snapshot — every week, including the
weeks the HTTP request succeeded.

The artefact proving it is in the export itself: `fred_raw.bin` is that ZIP, and
`fred_panel.csv` is a wide CSV whose most recent WALCL value is `2026-07-08` —
the last date a snapshot was successfully written.

## Contributing cause

The archives are large (~600 KB uncompressed for the daily member). Under a
30-second timeout with one blind retry, the pulls genuinely did time out
sometimes. That made the timeout story plausible enough to stop the
investigation there.

Requesting one series at a time returns plain CSV at 12–95 KB in well under a
second:

```
$ python -m liquidity_sentinel doctor
  ok    fred:WALCL                 23,225B  text  0.16s  attempt 1
  ok    fred:SOFR                  34,750B  text  0.13s  attempt 1
  WARN  fred:multi-series (legacy) 63,969B  ZIP archive  0.89s  attempt 1
```

## Why it stayed invisible

Three design choices hid it:

1. **A successful fetch and a successful parse were conflated.** HTTP 200 with
   zero parsed rows was indistinguishable from a network failure.
2. **Freshness was a single global boolean.** `fred_available` covered all
   fifteen series, so there was no per-series signal to contradict it.
3. **The only persistence was the latest snapshot.** With no observation
   history, "the data has not moved in six weeks" was not a question the
   tracker could ask itself.

## Fixes

| Cause | Fix |
| --- | --- |
| ZIP misread as CSV | Per-series requests; `Response.is_zip`; archive members merged if one ever appears |
| Oversized payloads | ~20 KB per series instead of ~600 KB per group |
| Blind single retry | Exponential backoff with jitter, 4 attempts, no retry on hard 4xx |
| All-or-nothing freshness | Per-series staleness in days, tuned to each series' publication lag |
| No history | SQLite mirror of every observation |
| Silent parse failure | `doctor` reports content kind per endpoint and flags ZIP responses |

## Two smaller bugs found on the way

**ECB was scraping navigation chrome.** The index page is a JS shell; the
release list is injected from per-year include files. The old regex scanned the
shell and returned the newest date anywhere in it — a promo tile. The exported
state records `"ecb_wfs_date": "23 July 2026"` with a headline reading "Quick
links What is monetary policy? Strategy review...". The real statement on that
date was published 2026-08-25. Fixed by fetching the year include, which carries
`<dt isoDate="...">` attributes.

**Net liquidity mixed observation dates.** `WALCL - WTREGEN - RRPONTSYD` combined
each series' own latest print. WALCL and WTREGEN are weekly (Wednesday);
RRPONTSYD is daily. A Friday RRP value was being subtracted from a Wednesday
balance sheet. Components are now sampled as-of the weekly anchor date.

## Regression coverage

`tests/test_fred.py::test_multiseries_response_is_a_zip_not_csv` asserts both
halves: that the text parse finds nothing (reproducing the original failure) and
that the ZIP-aware parse recovers the series. The fixture is a trimmed copy of a
real archive response, not a synthetic one.
