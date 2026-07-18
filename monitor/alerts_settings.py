"""Alert configuration from AppConfig thresholds with env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _config_webhook_url(app_config: Any | None) -> str | None:
    """Read webhook URL from AppConfig if a future/notifications field exists."""
    if app_config is None:
        return None
    direct = getattr(app_config, "webhook_url", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    notifications = getattr(app_config, "notifications", None)
    if notifications is not None:
        url = getattr(notifications, "webhook_url", None)
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


@dataclass(frozen=True)
class AlertSettings:
    """Threshold and notification settings for the alert engine."""

    webhook_url: str | None = None
    bandwidth_mbps_threshold: float | None = None
    recv_bps_threshold: float | None = None
    sent_bps_threshold: float | None = None
    bandwidth_sustained_seconds: float = 30.0
    error_delta_threshold: int = 10
    cooldown_seconds: float = 300.0
    notify_health_events: bool = True

    @property
    def bandwidth_enabled(self) -> bool:
        return (
            self.bandwidth_mbps_threshold is not None
            or self.recv_bps_threshold is not None
            or self.sent_bps_threshold is not None
        )

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.webhook_url)

    @classmethod
    def from_env(cls) -> AlertSettings:
        """Build settings from environment variables only."""
        return cls.resolve(app_config=None)

    @classmethod
    def resolve(cls, app_config: Any | None = None) -> AlertSettings:
        """Merge AppConfig thresholds with env overrides (env wins when set)."""
        thresholds = getattr(app_config, "thresholds", None) if app_config else None

        total_bps = getattr(thresholds, "total_bps", None) if thresholds else None
        recv_bps = getattr(thresholds, "recv_bps", None) if thresholds else None
        sent_bps = getattr(thresholds, "sent_bps", None) if thresholds else None
        sustained_errors = (
            getattr(thresholds, "sustained_errors", None) if thresholds else None
        )

        bandwidth_mbps = (
            float(total_bps) / 1_000_000 if total_bps is not None else None
        )
        env_mbps = _env_float("ALERT_BANDWIDTH_MBPS")
        if env_mbps is not None:
            bandwidth_mbps = env_mbps

        env_recv = _env_float("ALERT_RECV_BPS")
        if env_recv is not None:
            recv_bps = env_recv
        elif recv_bps is not None:
            recv_bps = float(recv_bps)

        env_sent = _env_float("ALERT_SENT_BPS")
        if env_sent is not None:
            sent_bps = env_sent
        elif sent_bps is not None:
            sent_bps = float(sent_bps)

        error_delta = 10
        if sustained_errors is not None:
            error_delta = int(sustained_errors)
        env_error = _env_int("ALERT_ERROR_DELTA")
        if env_error is not None:
            error_delta = env_error

        webhook_url = _config_webhook_url(app_config)
        env_webhook = os.environ.get("ALERT_WEBHOOK_URL")
        if env_webhook is not None:
            webhook_url = env_webhook.strip() or None

        sustained = _env_float("ALERT_BANDWIDTH_SUSTAINED_SECONDS")
        cooldown = _env_float("ALERT_COOLDOWN_SECONDS")
        return cls(
            webhook_url=webhook_url,
            bandwidth_mbps_threshold=bandwidth_mbps,
            recv_bps_threshold=recv_bps,
            sent_bps_threshold=sent_bps,
            bandwidth_sustained_seconds=(
                sustained if sustained is not None else 30.0
            ),
            error_delta_threshold=error_delta,
            cooldown_seconds=cooldown if cooldown is not None else 300.0,
            notify_health_events=_env_flag("ALERT_NOTIFY_HEALTH", True),
        )
