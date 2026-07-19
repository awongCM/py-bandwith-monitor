"""Allow `python -m eero_monitor` as an entry point."""

from eero_monitor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
