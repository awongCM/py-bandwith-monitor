"""Retention and rollup settings for SQLite history tiers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monitor.config import AppConfig, RetentionConfig


def _env_int(name: str) -> int | None:
    """Return an env int when set; None when unset/empty."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    return int(raw)


@dataclass(frozen=True)
class RetentionSettings:
    """History retention for raw samples and rollup tiers.

    Prefer :meth:`from_app_config` / :meth:`from_retention_config` when a
    config file is loaded. Environment variables override config values when set:
    ``MONITOR_RAW_RETENTION_DAYS``, ``MONITOR_MINUTE_RETENTION_DAYS``,
    ``MONITOR_HOURLY_RETENTION_DAYS``, ``MONITOR_DAILY_RETENTION_DAYS``,
    ``MONITOR_MAINTENANCE_INTERVAL_SAMPLES``.
    """

    raw_retention_days: int = 7
    minute_retention_days: int = 30
    hourly_retention_days: int = 90
    daily_retention_days: int = 365
    maintenance_interval_samples: int = 300

    def with_env_overrides(self) -> RetentionSettings:
        raw = _env_int("MONITOR_RAW_RETENTION_DAYS")
        minute = _env_int("MONITOR_MINUTE_RETENTION_DAYS")
        hourly = _env_int("MONITOR_HOURLY_RETENTION_DAYS")
        daily = _env_int("MONITOR_DAILY_RETENTION_DAYS")
        maintenance = _env_int("MONITOR_MAINTENANCE_INTERVAL_SAMPLES")
        return RetentionSettings(
            raw_retention_days=(
                raw if raw is not None else self.raw_retention_days
            ),
            minute_retention_days=(
                minute if minute is not None else self.minute_retention_days
            ),
            hourly_retention_days=(
                hourly if hourly is not None else self.hourly_retention_days
            ),
            daily_retention_days=(
                daily if daily is not None else self.daily_retention_days
            ),
            maintenance_interval_samples=(
                maintenance
                if maintenance is not None
                else self.maintenance_interval_samples
            ),
        )

    @classmethod
    def from_retention_config(
        cls,
        config: RetentionConfig,
        *,
        apply_env: bool = True,
    ) -> RetentionSettings:
        """Map ``AppConfig.retention`` YAML keys onto runtime settings."""
        settings = cls(
            raw_retention_days=config.days,
            minute_retention_days=(
                config.minute_samples_days
                if config.minute_samples_days is not None
                else 30
            ),
            hourly_retention_days=(
                config.hourly_samples_days
                if config.hourly_samples_days is not None
                else 90
            ),
            daily_retention_days=(
                config.daily_samples_days
                if config.daily_samples_days is not None
                else 365
            ),
        )
        return settings.with_env_overrides() if apply_env else settings

    @classmethod
    def from_app_config(
        cls,
        config: AppConfig,
        *,
        apply_env: bool = True,
    ) -> RetentionSettings:
        return cls.from_retention_config(config.retention, apply_env=apply_env)

    @classmethod
    def from_env(cls) -> RetentionSettings:
        """Defaults plus any env overrides (no config file)."""
        return cls().with_env_overrides()
