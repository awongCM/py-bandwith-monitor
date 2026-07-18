from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from monitor.agent_client import build_agent_payload, post_sample, resolve_agent_host_id
from monitor.models import AggregateRates, InterfaceRates, InterfaceStats


class AgentClientTests(unittest.TestCase):
    def test_resolve_host_id_explicit(self) -> None:
        self.assertEqual(resolve_agent_host_id("kitchen-pi"), "kitchen-pi")

    @patch("monitor.agent_client.socket.gethostname", return_value="Andy-MacBook.local")
    def test_resolve_host_id_hostname(self, _mock: MagicMock) -> None:
        self.assertEqual(resolve_agent_host_id(None), "Andy-MacBook.local")

    def test_build_payload(self) -> None:
        now = 1000.0
        sample = AggregateRates(
            timestamp=now,
            recv_bps=1.0,
            sent_bps=2.0,
            interfaces=(InterfaceRates("en0", now, 1.0, 2.0, 0.0, 0.0),),
        )
        payload = build_agent_payload(
            "laptop",
            sample,
            [
                InterfaceStats(
                    name="en0",
                    is_up=True,
                    speed_mbps=1000,
                    duplex="full",
                    mtu=1500,
                    bytes_recv=1,
                    bytes_sent=2,
                    packets_recv=1,
                    packets_sent=1,
                    errin=0,
                    errout=0,
                    dropin=0,
                    dropout=0,
                )
            ],
        )

        self.assertEqual(payload["host_id"], "laptop")
        self.assertEqual(payload["recv_bps"], 1.0)
        self.assertEqual(payload["interfaces"][0]["name"], "en0")
        self.assertEqual(payload["snapshots"][0]["name"], "en0")

    def test_post_sample_uses_bearer_token(self) -> None:
        client = MagicMock()
        response = client.post.return_value

        post_sample(
            client,
            server="http://hub.example/",
            token="secret",
            payload={"host_id": "laptop"},
        )

        client.post.assert_called_once_with(
            "http://hub.example/api/agents/samples",
            json={"host_id": "laptop"},
            headers={"Authorization": "Bearer secret"},
            timeout=10.0,
        )
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
