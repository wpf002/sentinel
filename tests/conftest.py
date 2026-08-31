from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liquidity_sentinel.config import load_config  # noqa: E402
from liquidity_sentinel.http import Response  # noqa: E402
from liquidity_sentinel.store import Store  # noqa: E402


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def cfg(tmp_path):
    """Config loaded from the repo's real TOML, with data redirected to tmp."""
    root = Path(__file__).resolve().parents[1]
    return load_config(root / "config" / "sentinel.toml", data_dir=tmp_path / "data")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def today() -> date:
    return date(2026, 8, 31)


def make_response(body: bytes | str, url: str = "https://example.test") -> Response:
    if isinstance(body, str):
        body = body.encode("utf-8")
    return Response(url=url, body=body, attempts=1, elapsed_seconds=0.01)


class RecordedFetcher:
    """Replays canned responses by URL substring. Never touches the network."""

    def __init__(self, routes: dict[str, bytes | str], default: Exception | None = None):
        self.routes = routes
        self.default = default
        self.calls: list[str] = []

    def __call__(self, url, http_cfg, **kwargs):
        self.calls.append(url)
        for needle, body in self.routes.items():
            if needle in url:
                return make_response(body, url)
        if self.default is not None:
            raise self.default
        raise AssertionError(f"unrouted URL in test: {url}")
