"""Command-line interface for the bandwidth monitor."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from monitor import __version__
from monitor.collector import BandwidthCollector, list_interface_stats
from monitor.formatting import bytes2human, rate2human
from monitor.models import AggregateRates, InterfaceStats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="Monitor local network interface bandwidth.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Print a one-shot view of monitored interfaces.",
    )
    snapshot_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output.",
    )
    _add_interface_filters(snapshot_parser)

    watch_parser = subparsers.add_parser(
        "watch",
        help="Continuously print live upload and download rates.",
    )
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between samples (default: 1.0).",
    )
    watch_parser.add_argument(
        "--history-size",
        type=int,
        default=3600,
        help="Number of in-memory samples to retain (default: 3600).",
    )
    watch_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output.",
    )
    _add_interface_filters(watch_parser)

    return parser


def _add_interface_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Only monitor interfaces matching a glob pattern. Can be repeated.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Exclude interfaces matching a glob pattern. Can be repeated.",
    )


def _interface_filters(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    include = tuple(args.include)
    exclude = tuple(args.exclude)
    return include, exclude


def print_snapshot(interfaces: Sequence[InterfaceStats]) -> None:
    if not interfaces:
        print("No monitored interfaces found.")
        return

    for interface in interfaces:
        print(f"{interface.name}:")
        print(
            "    stats       : "
            f"speed={interface.speed_mbps}Mbps, duplex={interface.duplex}, "
            f"mtu={interface.mtu}, up={'yes' if interface.is_up else 'no'}"
        )
        print(
            "    incoming    : "
            f"bytes={bytes2human(interface.bytes_recv)}, "
            f"pkts={interface.packets_recv}, errs={interface.errin}, "
            f"drops={interface.dropin}"
        )
        print(
            "    outgoing    : "
            f"bytes={bytes2human(interface.bytes_sent)}, "
            f"pkts={interface.packets_sent}, errs={interface.errout}, "
            f"drops={interface.dropout}"
        )


def print_watch_sample(sample: AggregateRates) -> None:
    print(
        f"[{sample.timestamp:.0f}] total down={rate2human(sample.recv_bps)} "
        f"up={rate2human(sample.sent_bps)} "
        f"combined={rate2human(sample.total_bps)}",
        flush=True,
    )
    for interface in sample.interfaces:
        print(
            f"  {interface.name:16} "
            f"down={rate2human(interface.recv_bps):>12} "
            f"up={rate2human(interface.sent_bps):>12}",
            flush=True,
        )


def run_snapshot(args: argparse.Namespace) -> int:
    include, exclude = _interface_filters(args)
    interfaces = list_interface_stats(
        include=include or None,
        exclude=exclude or None,
    )

    if args.json:
        print(json.dumps([item.to_dict() for item in interfaces], indent=2))
    else:
        print_snapshot(interfaces)

    return 0


def run_watch(args: argparse.Namespace) -> int:
    include, exclude = _interface_filters(args)
    collector = BandwidthCollector(
        interval=args.interval,
        history_size=args.history_size,
        include=include,
        exclude=exclude,
    )

    try:
        for sample in collector.watch():
            if args.json:
                print(json.dumps(sample.to_dict()), flush=True)
            else:
                print_watch_sample(sample)
    except KeyboardInterrupt:
        if not args.json:
            print("\nStopped.")
        return 0

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        return run_snapshot(args)
    if args.command == "watch":
        return run_watch(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
