"""Configuration loading.

All thresholds and endpoints live in ``config/sentinel.toml`` so the alert
policy can change without touching code. Environment variables supply secrets
only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/sentinel.toml")


@dataclass(frozen=True)
class RunConfig:
    timezone: str = "America/Chicago"
    weekdays: tuple[int, ...] = (5,)
    hour: int = 7
    enforce_window: bool = False


@dataclass(frozen=True)
class HttpConfig:
    timeout_seconds: float = 20.0
    max_attempts: int = 4
    backoff_base_seconds: float = 1.5
    backoff_max_seconds: float = 20.0
    user_agent: str = "LiquiditySentinel/0.2"


@dataclass(frozen=True)
class FredConfig:
    strategy: tuple[str, ...] = ("api", "csv")
    history_days: int = 900
    stale_after_days: int = 21
    api_key: str | None = None
    stale_overrides: dict[str, int] = field(default_factory=dict)

    def stale_days_for(self, series: str) -> int:
        """Staleness limit for *series*, honouring its publication lag."""
        return int(self.stale_overrides.get(series, self.stale_after_days))


@dataclass(frozen=True)
class StablecoinConfig:
    url: str = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
    stale_after_days: int = 10


@dataclass(frozen=True)
class EcbConfig:
    url: str = (
        "https://www.ecb.europa.eu/press/annual-reports-financial-statements/wfs/"
        "{year}/html/index_include.en.html"
    )
    enabled: bool = True


@dataclass(frozen=True)
class NotifyConfig:
    sinks: tuple[str, ...] = ("console", "file")
    dedupe_days: int = 6
    webhook_url: str | None = None


@dataclass(frozen=True)
class Config:
    run: RunConfig = field(default_factory=RunConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    fred: FredConfig = field(default_factory=FredConfig)
    stablecoins: StablecoinConfig = field(default_factory=StablecoinConfig)
    ecb: EcbConfig = field(default_factory=EcbConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    alerts: dict[str, float] = field(default_factory=dict)
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sentinel.db"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"


_DEFAULT_ALERTS: dict[str, float] = {
    "stablecoins_wow_pct": 0.01,
    "stablecoins_wow_usd": 2.0e9,
    "fed_net_liquidity_wow_bil": 100.0,
    "fed_component_wow_bil": 100.0,
    "reserves_drop_bil": 75.0,
    "reserves_floor_bil": 3000.0,
    "m2_wow_pct": 0.005,
    "ig_spread_wow_bps": 10.0,
    "hy_spread_wow_bps": 25.0,
    "nfci_wow": 0.10,
    "anfci_wow": 0.10,
    "yield_wow_bps": 20.0,
    "curve_wow_bps": 15.0,
    "policy_rate_wow_bps": 10.0,
    "funding_spread_widen_bps": 5.0,
    "regime_score_delta": 2,
}


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def load_config(path: Path | None = None, data_dir: Path | None = None) -> Config:
    """Load config from TOML, falling back to defaults for anything absent."""
    path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    run = _section(raw, "run")
    http = _section(raw, "http")
    fred = _section(raw, "fred")
    stable = _section(raw, "stablecoins")
    ecb = _section(raw, "ecb")
    notify = _section(raw, "notify")

    alerts = dict(_DEFAULT_ALERTS)
    alerts.update({k: float(v) for k, v in _section(raw, "alerts").items()})

    return Config(
        run=RunConfig(
            timezone=run.get("timezone", RunConfig.timezone),
            weekdays=tuple(run.get("weekdays", RunConfig.weekdays)),
            hour=int(run.get("hour", RunConfig.hour)),
            enforce_window=bool(run.get("enforce_window", RunConfig.enforce_window)),
        ),
        http=HttpConfig(
            timeout_seconds=float(http.get("timeout_seconds", HttpConfig.timeout_seconds)),
            max_attempts=int(http.get("max_attempts", HttpConfig.max_attempts)),
            backoff_base_seconds=float(
                http.get("backoff_base_seconds", HttpConfig.backoff_base_seconds)
            ),
            backoff_max_seconds=float(
                http.get("backoff_max_seconds", HttpConfig.backoff_max_seconds)
            ),
            user_agent=http.get("user_agent", HttpConfig.user_agent),
        ),
        fred=FredConfig(
            strategy=tuple(fred.get("strategy", FredConfig.strategy)),
            history_days=int(fred.get("history_days", FredConfig.history_days)),
            stale_after_days=int(fred.get("stale_after_days", FredConfig.stale_after_days)),
            api_key=os.environ.get("FRED_API_KEY") or None,
            stale_overrides={
                k: int(v) for k, v in (fred.get("stale_after_days_overrides") or {}).items()
            },
        ),
        stablecoins=StablecoinConfig(
            url=stable.get("url", StablecoinConfig.url),
            stale_after_days=int(stable.get("stale_after_days", StablecoinConfig.stale_after_days)),
        ),
        ecb=EcbConfig(
            url=ecb.get("url", EcbConfig.url),
            enabled=bool(ecb.get("enabled", EcbConfig.enabled)),
        ),
        notify=NotifyConfig(
            sinks=tuple(notify.get("sinks", NotifyConfig.sinks)),
            dedupe_days=int(notify.get("dedupe_days", NotifyConfig.dedupe_days)),
            webhook_url=os.environ.get("SENTINEL_WEBHOOK_URL") or None,
        ),
        alerts=alerts,
        data_dir=data_dir or Path("data"),
    )
