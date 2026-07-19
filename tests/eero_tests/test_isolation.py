from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EERO_DIR = ROOT / "eero_monitor"
MONITOR_DIR = ROOT / "monitor"


def _imports_of(package_dir: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == prefix or alias.name.startswith(f"{prefix}.")
                        for prefix in forbidden_prefixes
                    ):
                        offenders.append(f"{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if any(
                    mod == prefix or mod.startswith(f"{prefix}.")
                    for prefix in forbidden_prefixes
                ):
                    offenders.append(f"{path}:{mod}")
    return offenders


class IsolationTests(unittest.TestCase):
    def test_eero_monitor_does_not_import_monitor(self) -> None:
        self.assertEqual(_imports_of(EERO_DIR, ("monitor",)), [])

    def test_monitor_does_not_import_eero_monitor(self) -> None:
        self.assertEqual(_imports_of(MONITOR_DIR, ("eero_monitor",)), [])


if __name__ == "__main__":
    unittest.main()
