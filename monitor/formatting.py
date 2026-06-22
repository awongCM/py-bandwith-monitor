"""Human-readable formatting for byte counts and transfer rates."""

from __future__ import annotations

import psutil

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def bytes2human(value: int | float, *, precision: int = 1) -> str:
    """Format a byte count using binary prefixes."""
    amount = float(value)
    for unit in _UNITS:
        if abs(amount) < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.{precision}f} {unit}"
        amount /= 1024.0
    return f"{amount:.{precision}f} {_UNITS[-1]}"


def rate2human(bits_per_second: float, *, precision: int = 2) -> str:
    """Format a transfer rate in bits per second using decimal prefixes."""
    if bits_per_second < 0:
        bits_per_second = 0.0

    units = ("bit/s", "Kbit/s", "Mbit/s", "Gbit/s", "Tbit/s")
    amount = float(bits_per_second)
    for unit in units:
        if amount < 1000.0 or unit == units[-1]:
            if unit == "bit/s":
                return f"{amount:.{precision}f} {unit}"
            return f"{amount:.{precision}f} {unit}"
        amount /= 1000.0
    return f"{amount:.{precision}f} {units[-1]}"


def duplex_label(duplex: int) -> str:
    mapping = {
        psutil.NIC_DUPLEX_FULL: "full",
        psutil.NIC_DUPLEX_HALF: "half",
        psutil.NIC_DUPLEX_UNKNOWN: "?",
    }
    return mapping.get(duplex, "?")

