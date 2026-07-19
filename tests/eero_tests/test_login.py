from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from eero_monitor.login_flow import LoginError, obtain_credentials, run_login_flow


class ObtainCredentialsTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_verify_and_list_networks(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.is_authenticated = False
        client.login = AsyncMock(return_value=True)
        client.verify = AsyncMock(return_value=True)
        client._api.auth.get_auth_token = AsyncMock(return_value="session-token")
        client.get_networks = AsyncMock(
            return_value={
                "data": {
                    "networks": [
                        {"name": "Home", "url": "/2.2/networks/net-123"},
                    ]
                }
            }
        )

        def factory():
            return client

        session, networks = await obtain_credentials(
            "user@example.com",
            read_code=lambda: "123456",
            client_factory=factory,
        )
        self.assertEqual(session, "session-token")
        self.assertEqual(networks, [("Home", "net-123")])
        client.login.assert_awaited_once_with("user@example.com")
        client.verify.assert_awaited_once_with("123456")

    async def test_empty_token_raises(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.is_authenticated = True
        client._api.auth.get_auth_token = AsyncMock(return_value="")
        client.get_networks = AsyncMock(return_value={"data": {"networks": []}})

        with self.assertRaises(LoginError):
            await obtain_credentials(
                "user@example.com",
                read_code=lambda: "x",
                client_factory=lambda: client,
            )


class LoginCliTests(unittest.TestCase):
    def test_run_login_flow_prints_exports(self) -> None:
        buffer = io.StringIO()
        with patch(
            "eero_monitor.login_flow.obtain_credentials",
            new=AsyncMock(
                return_value=("tok", [("Home", "net-123")])
            ),
        ), redirect_stdout(buffer):
            code = run_login_flow(
                user_identifier="user@example.com",
                read_code=lambda: "123456",
            )
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("export EERO_SESSION=tok", output)
        self.assertIn("export EERO_NETWORK_ID=net-123", output)

    def test_cli_login_wires_through(self) -> None:
        from eero_monitor import cli

        with patch(
            "eero_monitor.cli.run_login",
            return_value=0,
        ) as run_login:
            code = cli.main(["login", "--user", "user@example.com"])
        self.assertEqual(code, 0)
        run_login.assert_called_once()
        self.assertEqual(run_login.call_args.args[0].user, "user@example.com")


if __name__ == "__main__":
    unittest.main()
