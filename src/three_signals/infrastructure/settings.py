"""Configuration read once from the environment and validated (twelve-factor)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(ValueError):
    """An environment variable is missing or has an unusable value."""


def _text(env: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = env.get(name, default)
    if value is None or value == "":
        raise SettingsError(f"{name} is required")
    return value


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as error:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from error
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _whole(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    return int(_number(env, name, default, low, high))


def _log_level(env: Mapping[str, str]) -> str:
    level = env.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise SettingsError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR, got {level!r}")
    return level


@dataclass(frozen=True, slots=True)
class BackSettings:
    """Everything the back service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    database_url: str
    pool_max_size: int
    slow_query_probability: float
    slow_query_seconds: float
    slow_query_seed: int | None
    slow_query_warn_seconds: float
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BackSettings:
        """Build the settings, raising ``SettingsError`` for a missing or invalid variable."""
        seed = env.get("SLOW_QUERY_SEED", "")
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "back"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=_whole(env, "BACK_PORT", 8081, 1, 65535),
            database_url=_text(env, "DATABASE_URL"),
            pool_max_size=_whole(env, "DB_POOL_MAX_SIZE", 8, 1, 64),
            slow_query_probability=_number(env, "SLOW_QUERY_PROBABILITY", 0.05, 0.0, 1.0),
            slow_query_seconds=_number(env, "SLOW_QUERY_SECONDS", 1.5, 0.0, 30.0),
            slow_query_seed=int(seed) if seed else None,
            slow_query_warn_seconds=_number(env, "SLOW_QUERY_WARN_SECONDS", 1.0, 0.0, 30.0),
            log_level=_log_level(env),
        )


@dataclass(frozen=True, slots=True)
class FrontSettings:
    """Everything the front service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    back_url: str
    back_timeout_seconds: float
    back_retries: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> FrontSettings:
        """Build the settings, raising ``SettingsError`` for a missing or invalid variable."""
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "front"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=_whole(env, "FRONT_PORT", 8080, 1, 65535),
            back_url=_text(env, "BACK_URL").rstrip("/"),
            back_timeout_seconds=_number(env, "BACK_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            back_retries=_whole(env, "BACK_RETRIES", 2, 0, 5),
            log_level=_log_level(env),
        )
