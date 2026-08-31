"""ECB weekly financial statement.

Informational only: the tracker records the latest publication so a new weekly
statement is visible, but ECB never gates a run or fires an alert.

The index page at ``/press/annual-reports-financial-statements/wfs/html/`` is a
shell -- the release list is injected client-side from per-year include files
named in its ``data-snippets`` attribute. Fetching the shell and scraping dates
out of it returns navigation chrome, which is why the exported state records an
``ecb_headline`` of "Quick links / Strategy review / Asset purchase programmes"
and a date belonging to an unrelated promo tile.

This module fetches the per-year include directly. It carries real markup:

    <dt isoDate="2026-08-25"><div class="date">25 August 2026</div></dt>
    <dd>... Consolidated financial statement of the Eurosystem as at 21 August 2026 ...

so the publication date comes from an attribute rather than a regex over prose.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from ..config import Config
from ..http import FetchError, fetch
from .base import SourceResult

YEAR_INCLUDE = (
    "https://www.ecb.europa.eu/press/annual-reports-financial-statements/wfs/"
    "{year}/html/index_include.en.html"
)

ENTRY = re.compile(
    r'<dt[^>]*\bisoDate="(?P<iso>\d{4}-\d{2}-\d{2})"[^>]*>(?P<dt>[\s\S]*?)</dt>'
    r'\s*<dd[^>]*>(?P<dd>[\s\S]*?)</dd>',
    re.I,
)
TITLE = re.compile(
    r'<div class="title">\s*<a[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<text>[\s\S]*?)</a>', re.I
)
TAG = re.compile(r"<[^>]+>")
# "... as at 21 August 2026" -- the statement's reference date, distinct from
# its publication date.
AS_AT = re.compile(
    r"as at\s+(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December)\s+20\d{2})",
    re.I,
)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", html)).strip()


def parse_year_include(html: str, *, today: date | None = None) -> dict | None:
    """Return the newest published weekly statement in *html*."""
    today = today or datetime.now(UTC).date()
    best: dict | None = None

    for match in ENTRY.finditer(html):
        try:
            published = date.fromisoformat(match.group("iso"))
        except ValueError:
            continue
        if published > today:
            continue  # scheduled but not yet released
        body = match.group("dd")
        if "weekly financial statement" not in _text(body).lower():
            continue
        title_match = TITLE.search(body)
        title = _text(title_match.group("text")) if title_match else _text(body)[:160]
        href = title_match.group("href") if title_match else None

        as_at = None
        as_at_match = AS_AT.search(title)
        if as_at_match:
            try:
                as_at = datetime.strptime(as_at_match.group(1), "%d %B %Y").date().isoformat()
            except ValueError:
                as_at = None

        entry = {
            "date": published.isoformat(),
            "date_text": _text(match.group("dt")),
            "title": title,
            "statement_as_at": as_at,
            "url": f"https://www.ecb.europa.eu{href}" if href and href.startswith("/") else href,
        }
        if best is None or entry["date"] > best["date"]:
            best = entry
    return best


def fetch_ecb(cfg: Config, *, fetcher=fetch, today: date | None = None) -> SourceResult:
    result = SourceResult(name="ecb")
    if not cfg.ecb.enabled:
        result.notes.append("disabled in config")
        return result

    today = today or datetime.now(UTC).date()
    # In early January the current year's include may still be empty.
    for year in (today.year, today.year - 1):
        url = (cfg.ecb.url or YEAR_INCLUDE).format(year=year)
        try:
            response = fetcher(url, cfg.http)
        except FetchError as exc:
            result.errors[str(year)] = exc.last_error
            result.attempts += exc.attempts
            continue
        result.attempts += response.attempts
        result.elapsed_seconds += response.elapsed_seconds
        entry = parse_year_include(response.text(), today=today)
        if entry:
            result.ok = True
            result.data = entry
            result.errors.clear()
            return result
        result.notes.append(f"{year}: no released statement found")

    if not result.errors:
        result.errors["parse"] = "no weekly financial statement found"
    return result
