from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EERO_DIR = ROOT / "eero_monitor"


class IsolationTests(unittest.TestCase):
    def test_eero_monitor_does_not_import_monitor(self) -> None:
        offenders: list[str] = []
        for path in EERO_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "monitor" or alias.name.startswith("monitor."):
                            offenders.append(f"{path}:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "monitor" or mod.startswith("monitor."):
                        offenders.append(f"{path}:{mod}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
