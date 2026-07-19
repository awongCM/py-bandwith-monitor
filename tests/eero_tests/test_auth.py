from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from eero_monitor.auth import AuthError, load_credentials


class AuthTests(unittest.TestCase):
    def test_missing_both_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AuthError) as ctx:
                load_credentials()
        message = str(ctx.exception)
        self.assertIn("EERO_SESSION", message)
        self.assertIn("EERO_NETWORK_ID", message)

    def test_partial_env_raises(self) -> None:
        with patch.dict(os.environ, {"EERO_SESSION": "tok"}, clear=True):
            with self.assertRaises(AuthError):
                load_credentials()

    def test_loads_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"EERO_SESSION": "tok", "EERO_NETWORK_ID": "net1"},
            clear=True,
        ):
            self.assertEqual(load_credentials(), ("tok", "net1"))


if __name__ == "__main__":
    unittest.main()
