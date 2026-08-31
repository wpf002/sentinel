"""Shared result type for data sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceResult:
    """Outcome of one source fetch.

    ``ok`` means the network call and parse both succeeded. A source can be
    ``ok=False`` and still contribute values, because callers fall back to the
    SQLite mirror; ``errors`` records why the live pull did not land.
    """

    name: str
    ok: bool = False
    data: Any = None
    errors: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    attempts: int = 0
    elapsed_seconds: float = 0.0

    @property
    def error_summary(self) -> str | None:
        if not self.errors:
            return None
        return "; ".join(f"{k}: {v}" for k, v in sorted(self.errors.items()))
