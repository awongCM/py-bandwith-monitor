from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from monitor.config import RetentionConfig, parse_config_data
from monitor.models import (
    AGGREGATE_INTERFACE,
    AggregateRates,
    InterfaceRates,
)
from monitor.retention import RetentionSettings
from monitor.storage import MetricsDatabase, choose_resolution, _floor_bucket


def _sample(
    timestamp: float,
    recv_bps: float,
    sent_bps: float,
    *,
    interface: str = "eth0",
) -> AggregateRates:
    iface = InterfaceRates(
        name=interface,
        timestamp=timestamp,
        recv_bps=recv_bps,
        sent_bps=sent_bps,
        recv_pps=1.0,
        sent_pps=2.0,
    )
    return AggregateRates(
        timestamp=timestamp,
        recv_bps=recv_bps,
        sent_bps=sent_bps,
        interfaces=(iface,),
    )


class RetentionRollupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = MetricsDatabase(Path(self.tempdir.name) / "test.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def test_minute_rollup_averages_raw_samples(self) -> None:
        minute_start = _floor_bucket(1_700_000_000.0, 60)
        self.db.insert_rates(_sample(minute_start + 10, 100.0, 200.0))
        self.db.insert_rates(_sample(minute_start + 20, 300.0, 500.0))

        self.db.rollup_raw_to_minute(before=minute_start + 60)

        rows = self.db._get_rollup_rate_history(
            "rate_samples_minute",
            AGGREGATE_INTERFACE,
            since=minute_start,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], minute_start)
        self.assertAlmostEqual(rows[0]["recv_bps"], 200.0)
        self.assertAlmostEqual(rows[0]["sent_bps"], 350.0)
        self.assertEqual(rows[0]["sample_count"], 2)

    def test_hourly_and_daily_rollups_chain(self) -> None:
        hour_start = _floor_bucket(1_700_000_000.0, 3600)
        day_start = _floor_bucket(hour_start, 86400)

        for offset in (0, 60, 120):
            self.db.insert_rates(
                _sample(hour_start + offset, 100.0 + offset, 200.0 + offset)
            )

        self.db.rollup_raw_to_minute(before=hour_start + 3600)
        self.db.rollup_minute_to_hourly(before=hour_start + 3600)

        hourly = self.db._get_rollup_rate_history(
            "rate_samples_hourly",
            AGGREGATE_INTERFACE,
            since=hour_start,
        )
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0]["timestamp"], hour_start)
        self.assertEqual(hourly[0]["sample_count"], 3)

        self.db.rollup_hourly_to_daily(before=day_start + 86400)
        daily = self.db._get_rollup_rate_history(
            "rate_samples_daily",
            AGGREGATE_INTERFACE,
            since=day_start,
        )
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["timestamp"], day_start)

    def test_retention_maintenance_prunes_old_raw_and_keeps_minute(self) -> None:
        settings = RetentionSettings(
            raw_retention_days=7,
            minute_retention_days=30,
            hourly_retention_days=90,
            daily_retention_days=365,
        )
        now = 1_800_000_000.0
        old_minute = now - (8 * 86400)
        old_minute = old_minute - (old_minute % 60)

        self.db.insert_rates(_sample(old_minute + 5, 1000.0, 2000.0))
        self.db.run_retention_maintenance(settings, now=now)

        raw = self.db._get_raw_rate_history(
            AGGREGATE_INTERFACE,
            since=old_minute,
        )
        self.assertEqual(raw, [])

        minute = self.db._get_rollup_rate_history(
            "rate_samples_minute",
            AGGREGATE_INTERFACE,
            since=old_minute,
        )
        self.assertEqual(len(minute), 1)
        self.assertAlmostEqual(minute[0]["recv_bps"], 1000.0)

    def test_minute_retention_prunes_old_minute_rollups(self) -> None:
        settings = RetentionSettings(
            raw_retention_days=7,
            minute_retention_days=30,
        )
        now = 1_800_000_000.0
        old_minute = now - (31 * 86400)
        old_minute = old_minute - (old_minute % 60)

        self.db.insert_rates(_sample(old_minute + 5, 500.0, 600.0))
        self.db.run_retention_maintenance(settings, now=now)

        minute = self.db._get_rollup_rate_history(
            "rate_samples_minute",
            AGGREGATE_INTERFACE,
            since=old_minute,
        )
        self.assertEqual(minute, [])

    def test_get_rate_history_uses_minute_resolution_for_long_ranges(self) -> None:
        minute_start = _floor_bucket(time.time() - 3600, 60)
        self.db.insert_rates(_sample(minute_start + 10, 100.0, 200.0))
        self.db.rollup_raw_to_minute(before=minute_start + 60)

        history = self.db.get_rate_history(
            AGGREGATE_INTERFACE,
            minutes=24 * 60,
            resolution="minute",
        )
        self.assertGreaterEqual(len(history), 1)
        self.assertIn("sample_count", history[0])

    def test_choose_resolution_auto(self) -> None:
        self.assertEqual(choose_resolution(60), "raw")
        self.assertEqual(choose_resolution(24 * 60), "minute")
        self.assertEqual(choose_resolution(60 * 24 * 60), "hour")
        self.assertEqual(choose_resolution(120 * 24 * 60), "day")

    def test_from_retention_config_maps_yaml_keys(self) -> None:
        settings = RetentionSettings.from_retention_config(
            RetentionConfig(
                days=14,
                minute_samples_days=21,
                hourly_samples_days=45,
                daily_samples_days=180,
            ),
            apply_env=False,
        )
        self.assertEqual(settings.raw_retention_days, 14)
        self.assertEqual(settings.minute_retention_days, 21)
        self.assertEqual(settings.hourly_retention_days, 45)
        self.assertEqual(settings.daily_retention_days, 180)

    def test_from_retention_config_defaults_null_rollup_keys(self) -> None:
        settings = RetentionSettings.from_retention_config(
            RetentionConfig(days=10),
            apply_env=False,
        )
        self.assertEqual(settings.raw_retention_days, 10)
        self.assertEqual(settings.minute_retention_days, 30)
        self.assertEqual(settings.hourly_retention_days, 90)
        self.assertEqual(settings.daily_retention_days, 365)

    def test_from_app_config_and_env_override(self) -> None:
        config = parse_config_data(
            {
                "retention": {
                    "days": 14,
                    "minute_samples_days": 21,
                }
            }
        )
        previous = os.environ.get("MONITOR_MINUTE_RETENTION_DAYS")
        try:
            os.environ["MONITOR_MINUTE_RETENTION_DAYS"] = "40"
            settings = RetentionSettings.from_app_config(config)
            self.assertEqual(settings.raw_retention_days, 14)
            self.assertEqual(settings.minute_retention_days, 40)
        finally:
            if previous is None:
                os.environ.pop("MONITOR_MINUTE_RETENTION_DAYS", None)
            else:
                os.environ["MONITOR_MINUTE_RETENTION_DAYS"] = previous


if __name__ == "__main__":
    unittest.main()
