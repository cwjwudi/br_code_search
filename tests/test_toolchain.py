from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from br_code_search.core import CodeSearchIndex
from br_code_search.toolchain import inspect_toolchain, normalize_report


class ToolchainAdapterTests(unittest.TestCase):
    def test_inspect_is_read_only_and_accepts_local_config_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "config" / "local").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "docs" / "PLC_AUTOMATION_TOOLCHAIN_CONTEXT.md").write_text("context", encoding="utf-8")
            (root / "docs" / "PLC_TOOLCHAIN_IMPLEMENTATION_PLAN.md").write_text("plan", encoding="utf-8")
            (root / "config" / "local" / "plc_targets.br_local.json").write_text("{}", encoding="utf-8")
            result = inspect_toolchain(root)
            self.assertTrue(result["ok"])
            self.assertTrue(result["read_only"])
            self.assertIn("build", result["blocked_operations"])
            self.assertEqual(5, len(result["checks"]))

    def test_normalize_mcp_report(self) -> None:
        normalized = normalize_report(
            {
                "structuredContent": {
                    "ok": False,
                    "tool": "plc_build_project",
                    "target": "arsim",
                    "summary": "build failed",
                    "data": {
                        "ok": False,
                        "errors": ["E123: invalid ST"],
                        "warnings": ["W1: deprecated"],
                        "package_path": "var/reports/demo.zip",
                        "as_version": "6.5.0",
                        "ar_version": "6.5.1",
                        "cpu_model": "X20CP3687X",
                    },
                }
            },
            report_path="C:/reports/build.json",
        )
        self.assertEqual("failed", normalized["status"])
        self.assertEqual(["E123: invalid ST"], normalized["errors"])
        self.assertEqual("6.5.0", normalized["as_version"])
        self.assertEqual("X20CP3687X", normalized["cpu_model"])
        self.assertEqual("var/reports/demo.zip", normalized["artifact"])

    def test_import_report_persists_build_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "Sample"
            (source / "Logical").mkdir(parents=True)
            (source / "Sample.apj").write_text("<?AutomationStudio Version=\"6.5\"?><Project />", encoding="utf-8")
            (source / "Logical" / "Main.st").write_text("PROGRAM Main\nEND_PROGRAM\n", encoding="utf-8")
            index = CodeSearchIndex(root / "index.sqlite3")
            index.rebuild(source.parent)
            report_path = root / "build.json"
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt-1",
                        "ok": True,
                        "tool": "plc_build_project",
                        "target": "arsim",
                        "config": "Config1",
                        "summary": "Build: 0 error(s), 2 warning(s)",
                        "data": {
                            "ok": True,
                            "warnings": ["W1: example"],
                            "logs": ["var/build.log"],
                            "next_actions": ["probe target"],
                            "package_path": "Binaries/Config1/RUCPackage.zip",
                            "as_version": "6.5.0",
                            "ar_version": "6.5.1",
                            "cpu_model": "X20CP3687X",
                        },
                    }
                ),
                encoding="utf-8",
            )
            imported = index.import_toolchain_report(report_path, project="Sample")
            self.assertEqual("passed", imported["record"]["status"])
            self.assertEqual("br-plc-toolchain", imported["record"]["source"])
            self.assertEqual("evt-1", imported["record"]["report_id"])
            self.assertEqual(["var/build.log"], imported["record"]["log_paths"])
            self.assertEqual(1, index.get_compile_history("Sample")["count"])
            self.assertEqual("X20CP3687X", index.get_compile_history("Sample")["latest"]["cpu_model"])
            self.assertEqual(1, index.get_compile_history("Sample", target="arsim", tool="plc_build_project")["count"])
            summary = index.get_build_diagnostic_summary()
            self.assertEqual(1, summary["record_count"])
            self.assertEqual(1, summary["warning_counts"][0]["count"])
