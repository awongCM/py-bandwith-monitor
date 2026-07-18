"""Load optional YAML startup configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from monitor.models import LOCAL_HOST_ID

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")


@dataclass(frozen=True)
class InterfaceConfig:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class SamplingConfig:
    interval: float = 1.0
    history_size: int = 3600


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    db: str = "monitor.db"
    host_id: str = LOCAL_HOST_ID


@dataclass(frozen=True)
class AgentsConfig:
    token: str | None = None


@dataclass(frozen=True)
class RetentionConfig:
    days: int = 7
    minute_samples_days: int | None = None
    hourly_samples_days: int | None = None
    daily_samples_days: int | None = None


@dataclass(frozen=True)
class ThresholdConfig:
    recv_bps: float | None = None
    sent_bps: float | None = None
    total_bps: float | None = None
    sustained_errors: int | None = None


@dataclass(frozen=True)
class NotificationsConfig:
    webhook_url: str | None = None


@dataclass(frozen=True)
class AppConfig:
    interfaces: InterfaceConfig = field(default_factory=InterfaceConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def default_config() -> AppConfig:
    return AppConfig()


def discover_config_path(explicit: str | Path | None = None) -> Path | None:
    """Return the config file path to load, or None when no file applies."""
    if explicit is not None:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None

    for name in DEFAULT_CONFIG_NAMES:
        path = Path.cwd() / name
        if path.is_file():
            return path
    return None


def _coerce_str_tuple(value: Any, *, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value)
    raise ValueError(f"{key} must be a string or list of strings")


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = data.get(name, {})
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return section


def parse_config_data(data: Mapping[str, Any]) -> AppConfig:
    """Parse a config mapping into structured settings."""
    interfaces = _section(data, "interfaces")
    sampling = _section(data, "sampling")
    server = _section(data, "server")
    agents = _section(data, "agents")
    retention = _section(data, "retention")
    thresholds = _section(data, "thresholds")
    notifications = _section(data, "notifications")

    return AppConfig(
        interfaces=InterfaceConfig(
            include=_coerce_str_tuple(interfaces.get("include"), key="interfaces.include"),
            exclude=_coerce_str_tuple(interfaces.get("exclude"), key="interfaces.exclude"),
        ),
        sampling=SamplingConfig(
            interval=float(sampling.get("interval", 1.0)),
            history_size=int(sampling.get("history_size", 3600)),
        ),
        server=ServerConfig(
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 8080)),
            db=str(server.get("db", "monitor.db")),
            host_id=str(server.get("host_id", LOCAL_HOST_ID)),
        ),
        agents=AgentsConfig(token=_optional_str(agents.get("token"))),
        retention=RetentionConfig(
            days=int(retention.get("days", 7)),
            minute_samples_days=_optional_int(retention.get("minute_samples_days")),
            hourly_samples_days=_optional_int(retention.get("hourly_samples_days")),
            daily_samples_days=_optional_int(retention.get("daily_samples_days")),
        ),
        thresholds=ThresholdConfig(
            recv_bps=_optional_float(thresholds.get("recv_bps")),
            sent_bps=_optional_float(thresholds.get("sent_bps")),
            total_bps=_optional_float(thresholds.get("total_bps")),
            sustained_errors=_optional_int(thresholds.get("sustained_errors")),
        ),
        notifications=NotificationsConfig(
            webhook_url=_optional_str(notifications.get("webhook_url")),
        ),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config from *path* or discovered defaults; else return defaults."""
    config_path = discover_config_path(path)
    if config_path is None:
        return default_config()

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to load config files. Install with: pip install pyyaml"
        ) from exc

    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if raw is None:
        return default_config()
    if not isinstance(raw, Mapping):
        raise ValueError(f"Config file must contain a mapping: {config_path}")

    return parse_config_data(raw)
