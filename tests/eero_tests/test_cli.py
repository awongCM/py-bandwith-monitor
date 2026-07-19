from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from eero_monitor import cli
from eero_monitor.models import DeviceSnapshot


class FakeClient:
    def list_device_samples(self):
        return [
            (
                DeviceSnapshot(
                    device_id="a",
                    name="Phone",
                    mac="aa:bb",
                    ip="1.2.3.4",
                    is_online=True,
                    connection="wifi",
                ),
                10.0,
                5.0,
            )
        ]


class CliTests(unittest.TestCase):
    def test_devices_json(self) -> None:
        buffer = io.StringIO()
        with patch.dict(
            os.environ,
            {"EERO_SESSION": "t", "EERO_NETWORK_ID": "n"},
            clear=True,
        ), patch("eero_monitor.cli.EeroClient", return_value=FakeClient()), redirect_stdout(
            buffer
        ):
            code = cli.main(["devices", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload[0]["device_id"], "a")
        self.assertEqual(payload[0]["recv_bps"], 10.0)

    def test_missing_credentials_exits_nonzero(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            code = cli.main(["devices"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
