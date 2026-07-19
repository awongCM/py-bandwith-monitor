"""Command-line interface for the Eero household monitor."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from typing import Sequence

from eero_monitor import __version__
from eero_monitor.auth import AuthError, load_credentials
from eero_monitor.client import EeroClient, ensure_eero_sdk
from eero_monitor.collector import DeviceCollector
from eero_monitor.formatting import rate2human
from eero_monitor.login_flow import LoginError
from eero_monitor.models import AggregateDeviceRates, DeviceSnapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eero_monitor",
        description="Monitor household device bandwidth via the Eero cloud API.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        help="One-time Eero login; print export lines for EERO_SESSION / EERO_NETWORK_ID.",
    )
    login_parser.add_argument(
        "--user",
        metavar="EMAIL_OR_PHONE",
        help="Eero account email or phone (prompted if omitted).",
    )

    devices_parser = subparsers.add_parser(
        "devices",
        help="List known household devices once.",
    )
    devices_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output.",
    )

    watch_parser = subparsers.add_parser(
        "watch",
        help="Continuously print live per-device rates.",
    )
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between samples (default: 5.0).",
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

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the household dashboard and background sampler.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8081)
    serve_parser.add_argument("--db", default="eero_monitor.db")
    serve_parser.add_argument("--interval", type=float, default=5.0)
    serve_parser.add_argument("--history-size", type=int, default=3600)
    serve_parser.add_argument("--retention-days", type=int, default=7)

    return parser


def _build_client() -> EeroClient:
    session, network_id = load_credentials()
    return EeroClient(session, network_id)


def print_devices(
    samples: list[tuple[DeviceSnapshot, float, float]],
    *,
    as_json: bool,
) -> None:
    if as_json:
        payload = []
        for snapshot, recv_bps, sent_bps in samples:
            row = snapshot.to_dict()
            row["recv_bps"] = recv_bps
            row["sent_bps"] = sent_bps
            payload.append(row)
        print(json.dumps(payload, indent=2))
        return

    if not samples:
        print("No household devices found.")
        return

    for snapshot, recv_bps, sent_bps in samples:
        status = "online" if snapshot.is_online else "offline"
        print(
            f"{snapshot.name} ({snapshot.device_id}) [{status}] "
            f"{snapshot.connection} ip={snapshot.ip or '-'} "
            f"down={rate2human(recv_bps)} up={rate2human(sent_bps)}"
        )


def print_watch_sample(sample: AggregateDeviceRates) -> None:
    print(
        f"[{sample.timestamp:.0f}] household down={rate2human(sample.recv_bps)} "
        f"up={rate2human(sample.sent_bps)}",
        flush=True,
    )
    for device in sample.devices:
        status = "online" if device.is_online else "offline"
        print(
            f"  {device.name:20} [{status:7}] "
            f"down={rate2human(device.recv_bps):>12} "
            f"up={rate2human(device.sent_bps):>12}",
            flush=True,
        )


def run_login(args: argparse.Namespace) -> int:
    from eero_monitor.login_flow import run_login_flow

    user = (args.user or "").strip()
    if not user:
        try:
            user = input("Eero email or phone: ").strip()
        except EOFError as exc:
            raise LoginError("Email or phone is required (pass --user).") from exc
    if not user:
        raise LoginError("Email or phone is required (pass --user).")

    def read_code() -> str:
        try:
            return input("Verification code: ")
        except EOFError as exc:
            raise LoginError("Verification code is required.") from exc

    if sys.stderr.isatty():
        print(
            "Logging in via unofficial eero-api. "
            "Amazon-only accounts need a secondary email/password admin.",
            file=sys.stderr,
        )
    return run_login_flow(user_identifier=user, read_code=read_code)


def run_devices(args: argparse.Namespace) -> int:
    client = _build_client()
    ensure_eero_sdk()
    print_devices(client.list_device_samples(), as_json=args.json)
    return 0


def run_watch(args: argparse.Namespace) -> int:
    client = _build_client()
    ensure_eero_sdk()
    collector = DeviceCollector(client, interval=args.interval)
    stop_requested = False

    def request_stop(_signum: int, _frame: object | None) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        if sys.stderr.isatty() and not args.json:
            print(
                "Watching household rates via Eero. Press Ctrl+C to stop.",
                file=sys.stderr,
                flush=True,
            )
        for sample in collector.watch(
            max_samples=args.samples,
            duration=args.duration,
            stop_check=lambda: stop_requested,
        ):
            if args.json:
                print(json.dumps(sample.to_dict()), flush=True)
            else:
                print_watch_sample(sample)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    return 0


def run_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        print(
            "uvicorn is required for serve. Install with: pip install -r requirements-eero.txt",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    from eero_monitor.server import create_app

    # Fail fast on missing credentials or SDK before binding the port.
    load_credentials()
    ensure_eero_sdk()
    app = create_app(
        db_path=args.db,
        interval=args.interval,
        history_size=args.history_size,
        retention_days=args.retention_days,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handlers = {
        "login": run_login,
        "devices": run_devices,
        "watch": run_watch,
        "serve": run_serve,
    }
    try:
        return handlers[args.command](args)
    except (AuthError, LoginError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
