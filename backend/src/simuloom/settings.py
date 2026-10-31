"""Runtime settings, read from environment variables (prefix SIMULOOM_). No secrets are defaulted."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

APP_VERSION = "0.1.0"
DEFAULT_DATABASE_URL = ""
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw in (None, "") else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    start_supervisor: bool = True
    lease_seconds: float = 30.0
    heartbeat_seconds: float = 5.0
    sweep_seconds: float = 10.0
    poll_seconds: float = 1.0
    experiment_timeout_seconds: float = 120.0
    max_pending: int = 5
    max_total_visitor_simulations: int = 500_000
    log_level: str = "INFO"
    extra: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_env() -> Settings:
        origins = os.environ.get("SIMULOOM_CORS_ORIGINS", "")
        parsed = tuple(o.strip() for o in origins.split(",") if o.strip())
        d = Settings()
        return Settings(
            database_url=os.environ.get("SIMULOOM_DATABASE_URL") or d.database_url,
            cors_origins=parsed or d.cors_origins,
            start_supervisor=_env_bool("SIMULOOM_START_SUPERVISOR", d.start_supervisor),
            lease_seconds=_env_float("SIMULOOM_LEASE_SECONDS", d.lease_seconds),
            heartbeat_seconds=_env_float("SIMULOOM_HEARTBEAT_SECONDS", d.heartbeat_seconds),
            sweep_seconds=_env_float("SIMULOOM_SWEEP_SECONDS", d.sweep_seconds),
            poll_seconds=_env_float("SIMULOOM_POLL_SECONDS", d.poll_seconds),
            experiment_timeout_seconds=_env_float("SIMULOOM_EXPERIMENT_TIMEOUT_SECONDS", d.experiment_timeout_seconds),
            max_pending=_env_int("SIMULOOM_MAX_PENDING", d.max_pending),
            max_total_visitor_simulations=_env_int("SIMULOOM_MAX_VISITOR_SIMULATIONS", d.max_total_visitor_simulations),
            log_level=os.environ.get("SIMULOOM_LOG_LEVEL", d.log_level),
        )
