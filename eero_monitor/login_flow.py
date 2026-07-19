"""One-time interactive login to obtain EERO_SESSION / EERO_NETWORK_ID."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import Any


class LoginError(Exception):
    """Interactive Eero login failed."""


def _default_client_factory() -> Any:
    try:
        from eero import EeroClient
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise LoginError(
            "eero-api is required for login. "
            "Install with: pip install -r requirements-eero.txt "
            "(Python 3.12+ recommended for the live SDK)."
        ) from exc
    return EeroClient()


def _extract_networks(response: Any) -> list[tuple[str, str]]:
    data = response
    if isinstance(response, dict):
        data = response.get("data", response)

    networks: Any = data
    if isinstance(data, dict):
        networks = data.get("networks", data)

    if isinstance(networks, dict):
        # Some payloads nest again under data.
        nested = networks.get("data")
        if isinstance(nested, list):
            networks = nested
        else:
            networks = list(networks.values())

    if not isinstance(networks, list):
        return []

    results: list[tuple[str, str]] = []
    for net in networks:
        if not isinstance(net, dict):
            continue
        name = str(net.get("name") or "network")
        net_id = net.get("id")
        if not net_id:
            url = str(net.get("url") or "").rstrip("/")
            net_id = url.split("/")[-1] if url else None
        if net_id:
            results.append((name, str(net_id)))
    return results


async def obtain_credentials(
    user_identifier: str,
    *,
    read_code: Callable[[], str],
    client_factory: Callable[[], Any] | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Login + verify, then return ``(session_token, [(name, network_id), ...])``."""
    factory = client_factory or _default_client_factory
    client = factory()

    async with client:
        if not getattr(client, "is_authenticated", False):
            ok = await client.login(user_identifier)
            if not ok:
                raise LoginError("Login request failed. Check the email/phone and try again.")
            code = read_code().strip()
            if not code:
                raise LoginError("Verification code is required.")
            await client.verify(code)

        token = await client._api.auth.get_auth_token()
        if not token:
            raise LoginError("Login succeeded but no session token was returned.")

        response = await client.get_networks()
        networks = _extract_networks(response)
        return str(token), networks


def format_export_lines(session: str, networks: list[tuple[str, str]]) -> str:
    lines = [
        "# Paste these into your shell (session tokens are secrets — do not commit them).",
        f"export EERO_SESSION={session}",
    ]
    if not networks:
        lines.append(
            "# No networks found on this account. "
            "Set EERO_NETWORK_ID manually if you know it."
        )
    elif len(networks) == 1:
        name, net_id = networks[0]
        lines.append(f"# network: {name}")
        lines.append(f"export EERO_NETWORK_ID={net_id}")
    else:
        lines.append("# Multiple networks found — pick one:")
        for name, net_id in networks:
            lines.append(f"#   {name}: export EERO_NETWORK_ID={net_id}")
        lines.append(f"export EERO_NETWORK_ID={networks[0][1]}")
    return "\n".join(lines) + "\n"


def run_login_flow(
    *,
    user_identifier: str,
    read_code: Callable[[], str],
    client_factory: Callable[[], Any] | None = None,
) -> int:
    session, networks = asyncio.run(
        obtain_credentials(
            user_identifier,
            read_code=read_code,
            client_factory=client_factory,
        )
    )
    sys.stdout.write(format_export_lines(session, networks))
    return 0
