"""Command-line interface for the bandwidth monitor."""

from __future__ import annotations

import argparse
import json
import signal
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
    watch_parser.add_argument(
        "--duration",
        type=float,
        metavar="SECONDS",
        help="Stop automatically after this many seconds.",
    )
    watch_parser.add_argument(
        "--samples",
        type=int,
        metavar="N",
        help="Stop automatically after N samples.",
    )
    _add_interface_filters(watch_parser)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the web dashboard and background sampler.",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1).",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind (default: 8080).",
    )
    serve_parser.add_argument(
        "--db",
        default="monitor.db",
        help="SQLite database path (default: monitor.db).",
    )
    serve_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between background samples (default: 1.0).",
    )
    serve_parser.add_argument(
        "--history-size",
        type=int,
        default=3600,
        help="In-memory ring buffer size for the collector (default: 3600).",
    )
    serve_parser.add_argument(
        "--retention-days",
        type=int,
        default=7,
        help="Days of SQLite history to retain (default: 7).",
    )
    _add_interface_filters(serve_parser)

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
    stop_requested = False
    finished_by_limit = False

    def request_stop(_signum: int, _frame: object | None) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    if sys.stderr.isatty() and not args.json:
        print(
            "Watching bandwidth. Press Ctrl+C to stop "
            "(use --duration or --samples in cloud terminals).",
            file=sys.stderr,
            flush=True,
        )

    try:
        for sample in collector.watch(
            max_samples=args.samples,
            duration=args.duration,
            stop_check=lambda: stop_requested,
        ):
            if stop_requested:
                break
            if args.json:
                print(json.dumps(sample.to_dict()), flush=True)
            else:
                print_watch_sample(sample)
        else:
            finished_by_limit = bool(args.duration is not None or args.samples is not None)
    except KeyboardInterrupt:
        stop_requested = True
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    if not args.json:
        if stop_requested:
            print("\nStopped.", flush=True)
        elif finished_by_limit:
            print("\nFinished.", flush=True)
    return 0


def run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from monitor.server import create_app

    include, exclude = _interface_filters(args)
    app = create_app(
        db_path=args.db,
        interval=args.interval,
        history_size=args.history_size,
        include=include,
        exclude=exclude,
        retention_days=args.retention_days,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        return run_snapshot(args)
    if args.command == "watch":
        return run_watch(args)
    if args.command == "serve":
        return run_serve(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
