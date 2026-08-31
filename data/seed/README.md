# Seed data

`fred_history.csv` is the wide FRED history exported by the original tracker
(`tracker_files/fred_panel.csv`): 17,350 rows spanning 1962-01-02 to 2026-07-10
across all 15 tracked series.

`sentinel migrate` loads it into the SQLite mirror so the first local run has a
real week-prior comparison instead of starting cold.
