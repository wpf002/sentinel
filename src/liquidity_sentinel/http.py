"""HTTP fetching with bounded retries and ZIP-awareness.

Two failure modes drove this module:

1. FRED CSV requests time out intermittently. A single attempt with no backoff
   turned a transient blip into a permanently stale snapshot.
2. ``fredgraph.csv?id=A,B,C`` does not return CSV when the requested series have
   mixed frequencies -- it returns a ZIP of one CSV per frequency. Decoding those
   bytes as text yields zero parsable rows, which reads as "source unavailable"
   even though the request succeeded.

``fetch`` therefore retries with exponential backoff plus jitter, and reports the
detected content kind so callers can unpack a ZIP instead of misreading it.
"""

from __future__ import annotations

import functools
import io
import random
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from .config import HttpConfig

ZIP_MAGIC = b"PK\x03\x04"


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Verification context, preferring certifi's bundle when it is installed.

    Some publishers (the ECB among them) serve an incomplete certificate chain.
    System curl resolves it from the OS keychain; Python's default context does
    not, and fails with CERTIFICATE_VERIFY_FAILED. Installing the optional
    ``certifi`` extra fixes those hosts without weakening verification anywhere.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass(frozen=True)
class Response:
    url: str
    body: bytes
    attempts: int
    elapsed_seconds: float

    @property
    def is_zip(self) -> bool:
        return self.body.startswith(ZIP_MAGIC)

    def text(self, encoding: str = "utf-8-sig") -> str:
        return self.body.decode(encoding, errors="replace")

    def zip_members(self, suffix: str = ".csv") -> dict[str, str]:
        """Return ``{member_name: text}`` for archive members matching *suffix*."""
        if not self.is_zip:
            return {}
        out: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(self.body)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(suffix):
                    out[name] = zf.read(name).decode("utf-8-sig", errors="replace")
        return out


class FetchError(RuntimeError):
    """Raised when every attempt for a URL failed."""

    def __init__(self, url: str, attempts: int, last_error: str):
        super().__init__(f"{url}: failed after {attempts} attempt(s): {last_error}")
        self.url = url
        self.attempts = attempts
        self.last_error = last_error


def _sleep_for(attempt: int, cfg: HttpConfig, rng: random.Random) -> float:
    """Exponential backoff with full jitter, capped."""
    raw = cfg.backoff_base_seconds * (2 ** (attempt - 1))
    return rng.uniform(0.0, min(raw, cfg.backoff_max_seconds))


def fetch(
    url: str,
    cfg: HttpConfig,
    *,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
    clock=time.monotonic,
    rng: random.Random | None = None,
) -> Response:
    """GET *url*, retrying transient failures. Raises :class:`FetchError`."""
    rng = rng or random.Random(url)
    started = clock()
    last_error = "no attempt made"
    attempts = max(1, cfg.max_attempts)

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": cfg.user_agent, "Accept-Encoding": "identity"}
        )
        try:
            with opener(request, timeout=cfg.timeout_seconds, context=ssl_context()) as response:
                body = response.read()
            if not body:
                raise ValueError("empty response body")
            return Response(url, body, attempt, clock() - started)
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            # 4xx other than 429 will not fix themselves; stop early.
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim in FetchError
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            sleep(_sleep_for(attempt, cfg, rng))

    raise FetchError(url, attempts, last_error)


def post_json(url: str, payload: dict, cfg: HttpConfig) -> int:
    """POST a JSON body. Used by the webhook notifier; single attempt."""
    import json

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": cfg.user_agent},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=cfg.timeout_seconds, context=ssl_context()
    ) as response:
        return response.status
