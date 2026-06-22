"""Allow `python -m monitor` as an entry point."""

from monitor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
