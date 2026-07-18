from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from monitor.config import (
    AppConfig,
    InterfaceConfig,
    RetentionConfig,
    SamplingConfig,
    ServerConfig,
    discover_config_path,
    load_config,
    parse_config_data,
)
from monitor.cli import apply_config_defaults


class ConfigParsingTests(unittest.TestCase):
    def test_default_config(self) -> None:
        config = parse_config_data({})
        self.assertEqual(config.sampling.interval, 1.0)
        self.assertEqual(config.server.port, 8080)
        self.assertEqual(config.retention.days, 7)
        self.assertIsNone(config.thresholds.total_bps)
        self.assertIsNone(config.notifications.webhook_url)

    def test_parse_full_config(self) -> None:
        config = parse_config_data(
            {
                "interfaces": {"include": ["en0"], "exclude": ["utun*"]},
                "sampling": {"interval": 2.0, "history_size": 7200},
                "server": {"host": "0.0.0.0", "port": 9000, "db": "data/monitor.db"},
                "retention": {
                    "days": 14,
                    "minute_samples_days": 7,
                    "hourly_samples_days": 30,
                },
                "thresholds": {"total_bps": 100_000_000},
                "notifications": {
                    "webhook_url": "https://hooks.example.com/alert",
                },
            }
        )
        self.assertEqual(config.interfaces.include, ("en0",))
        self.assertEqual(config.interfaces.exclude, ("utun*",))
        self.assertEqual(config.sampling.interval, 2.0)
        self.assertEqual(config.server.host, "0.0.0.0")
        self.assertEqual(config.retention.hourly_samples_days, 30)
        self.assertEqual(config.thresholds.total_bps, 100_000_000.0)
        self.assertEqual(
            config.notifications.webhook_url,
            "https://hooks.example.com/alert",
        )

    def test_parse_agents_and_host_id(self) -> None:
        config = parse_config_data(
            {
                "server": {"host_id": "hub-pi"},
                "agents": {"token": "secret"},
            }
        )
        self.assertEqual(config.server.host_id, "hub-pi")
        self.assertEqual(config.agents.token, "secret")

    def test_empty_webhook_url_becomes_none(self) -> None:
        config = parse_config_data({"notifications": {"webhook_url": "  "}})
        self.assertIsNone(config.notifications.webhook_url)

    def test_load_config_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "sampling:",
                        "  interval: 0.5",
                        "server:",
                        "  port: 9090",
                    ]
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.sampling.interval, 0.5)
            self.assertEqual(config.server.port, 9090)

    def test_load_config_missing_file_returns_defaults(self) -> None:
        config = load_config("/tmp/does-not-exist-monitor-config.yaml")
        self.assertIsInstance(config, AppConfig)
        self.assertEqual(config.sampling.history_size, 3600)

    def test_discover_config_path_prefers_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            explicit = Path(tmpdir) / "custom.yaml"
            explicit.write_text("sampling:\n  interval: 3\n", encoding="utf-8")
            self.assertEqual(discover_config_path(explicit), explicit)

    def test_apply_config_defaults_for_serve(self) -> None:
        args = argparse.Namespace(
            command="serve",
            include=[],
            exclude=[],
            host=None,
            port=None,
            db=None,
            interval=None,
            history_size=None,
            retention_days=None,
        )
        config = AppConfig(
            interfaces=InterfaceConfig(include=("en0",), exclude=("utun*",)),
            sampling=SamplingConfig(interval=2.0, history_size=7200),
            server=ServerConfig(host="0.0.0.0", port=9000, db="data/monitor.db"),
            retention=RetentionConfig(days=14),
        )
        apply_config_defaults(args, config)
        self.assertEqual(args.include, ["en0"])
        self.assertEqual(args.exclude, ["utun*"])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9000)
        self.assertEqual(args.retention_days, 14)

    def test_explicit_cli_defaults_override_config(self) -> None:
        args = argparse.Namespace(
            command="serve",
            include=[],
            exclude=[],
            host="127.0.0.1",
            port=8080,
            db="monitor.db",
            interval=1.0,
            history_size=3600,
            retention_days=7,
        )
        config = AppConfig(
            sampling=SamplingConfig(interval=2.0, history_size=7200),
            server=ServerConfig(host="0.0.0.0", port=9000, db="data/monitor.db"),
            retention=RetentionConfig(days=14),
        )
        apply_config_defaults(args, config)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8080)
        self.assertEqual(args.db, "monitor.db")
        self.assertEqual(args.interval, 1.0)
        self.assertEqual(args.history_size, 3600)
        self.assertEqual(args.retention_days, 7)


if __name__ == "__main__":
    unittest.main()
