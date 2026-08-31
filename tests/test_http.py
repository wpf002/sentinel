"""Retry, backoff and content-kind detection."""

from __future__ import annotations

import urllib.error

import pytest

from liquidity_sentinel.config import HttpConfig
from liquidity_sentinel.http import FetchError, fetch


class FakeOpener:
    """Fails a fixed number of times, then returns *body*."""

    def __init__(self, failures: int, body: bytes = b"ok", exc=None):
        self.failures = failures
        self.body = body
        self.exc = exc or TimeoutError("read timed out")
        self.calls = 0

    def __call__(self, request, timeout=None, context=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return _Ctx(self.body)


class _Ctx:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


@pytest.fixture
def http_cfg():
    return HttpConfig(timeout_seconds=1, max_attempts=4, backoff_base_seconds=0.01,
                      backoff_max_seconds=0.02)


def test_succeeds_after_transient_failures(http_cfg):
    opener = FakeOpener(failures=2)
    slept = []
    response = fetch("https://x.test", http_cfg, opener=opener, sleep=slept.append)
    assert response.attempts == 3
    assert len(slept) == 2, "one backoff per retry"
    assert all(s <= http_cfg.backoff_max_seconds for s in slept), "backoff stays capped"


def test_raises_after_exhausting_attempts(http_cfg):
    opener = FakeOpener(failures=99)
    with pytest.raises(FetchError) as exc:
        fetch("https://x.test", http_cfg, opener=opener, sleep=lambda _: None)
    assert exc.value.attempts == 4
    assert "TimeoutError" in exc.value.last_error


def test_hard_4xx_stops_immediately(http_cfg):
    """A 404 will not fix itself; burning four attempts on it wastes the window."""

    def opener(request, timeout=None, context=None):
        raise urllib.error.HTTPError("https://x.test", 404, "Not Found", {}, None)

    with pytest.raises(FetchError):
        fetch("https://x.test", http_cfg, opener=opener, sleep=lambda _: None)


def test_429_is_retried(http_cfg):
    calls = []

    def opener(request, timeout=None, context=None):
        calls.append(1)
        raise urllib.error.HTTPError("https://x.test", 429, "Too Many", {}, None)

    with pytest.raises(FetchError):
        fetch("https://x.test", http_cfg, opener=opener, sleep=lambda _: None)
    assert len(calls) == 4, "rate limiting is transient and must be retried"


def test_empty_body_counts_as_failure(http_cfg):
    opener = FakeOpener(failures=0, body=b"")
    with pytest.raises(FetchError) as exc:
        fetch("https://x.test", http_cfg, opener=opener, sleep=lambda _: None)
    assert "empty response body" in exc.value.last_error


def test_detects_zip_bodies(http_cfg):
    opener = FakeOpener(failures=0, body=b"PK\x03\x04rest")
    assert fetch("https://x.test", http_cfg, opener=opener, sleep=lambda _: None).is_zip


def test_backoff_is_bounded_and_jittered(http_cfg):
    from random import Random

    from liquidity_sentinel.http import _sleep_for

    rng = Random(0)
    values = [_sleep_for(a, http_cfg, rng) for a in range(1, 8)]
    assert all(0 <= v <= http_cfg.backoff_max_seconds for v in values)
    assert len(set(values)) > 1, "jitter must vary, so retries do not synchronise"
