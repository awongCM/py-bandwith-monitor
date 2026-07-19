"""Read Eero credentials from the environment."""

from __future__ import annotations

import os


class AuthError(Exception):
    """Missing or invalid Eero credentials."""


def load_credentials() -> tuple[str, str]:
    """Return ``(session, network_id)`` or raise :class:`AuthError`."""
    session = (os.environ.get("EERO_SESSION") or "").strip()
    network_id = (os.environ.get("EERO_NETWORK_ID") or "").strip()
    if not session or not network_id:
        raise AuthError(
            "Missing Eero credentials. Set EERO_SESSION and EERO_NETWORK_ID "
            "(see README: Optional Eero household monitor)."
        )
    return session, network_id
