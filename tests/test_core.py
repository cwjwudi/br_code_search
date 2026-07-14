from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from br_code_search.core import (
    CodeSearchIndex,
    parse_st_units,
    parse_type_units,
    parse_var_units,
    read_source,
)


PROGRAM = """PROGRAM DemoProgram
VAR
    Axis : MpAxisBasic;
END_VAR

Axis.Enable := TRUE;
Axis();
END_PROGRAM
"""


class ParserTests(unittest.TestCase):
    def test_structured_text_unit(self) -> None:
        units = parse_st_units(PROGRAM)
        self.assertEqual(1, len(units))
        self.assertEqual("DemoProgram", units[0].symbol_name)
        self.assertEqual("program", units[0].symbol_type)
        self.assertIn("Axis();", units[0].content)

    def test_variable_symbols(self) -> None:
        units = parse_var_units("VAR_GLOBAL\n Ready : BOOL;\nEND_VAR\n", "Global")
        self.assertEqual(["Global:VAR_GLOBAL:1", "Ready"], [unit.symbol_name for unit in units])

    def test_type_symbol(self) -> None:
        units = parse_type_units("TYPE\nDemoType : STRUCT\n Value : INT;\nEND_STRUCT;\nEND_TYPE\n")
        self.assertEqual("DemoType", units[0].symbol_name)
        self.assertEqual("data_type", units[0].symbol_type)

    def test_cp1252_is_not_misclassified_as_gb18030(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "German.st"
            path.write_bytes("// müsste funktionieren".encode("cp1252"))
            text, encoding = read_source(path)
            self.assertEqual("cp1252", encoding)
            self.assertIn("müsste", text)

    def test_gb18030_comments_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Chinese.st"
            path.write_bytes("// 中文注释".encode("gb18030"))
            text, encoding = read_source(path)
            self.assertEqual("gb18030", encoding)
            self.assertIn("中文注释", text)


class IndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = root / "reference"
        self.project = self.source / "ProjectA"
        module = self.project / "Logical" / "Control"
        module.mkdir(parents=True)
        (self.project / "Demo.apj").write_text(
            '<?xml version="1.0"?><\u003fAutomationStudio Version="4.12.5.95 SP"?>'
            '<Project Version="1.2.3" Description="Demo" xmlns="http://br-automation.co.at/AS/Project">'
            '<TechnologyPackages><mapp Version="5.24.1" /></TechnologyPackages></Project>',
            encoding="utf-8",
        )
        (module / "Cyclic.st").write_text(PROGRAM, encoding="utf-8")
        (module / "Init.st").write_text(
            "PROGRAM _INIT\nReady := TRUE;\nEND_PROGRAM\n", encoding="utf-8"
        )
        (module / "Variables.var").write_text(
            "VAR\n Ready : BOOL;\nEND_VAR\n", encoding="utf-8"
        )
        (module / "Types.typ").write_text(
            "TYPE\nDemoType : STRUCT\n Value : INT;\nEND_STRUCT;\nEND_TYPE\n", encoding="utf-8"
        )
        library = self.project / "Logical" / "Libraries" / "Vendor"
        library.mkdir(parents=True)
        (library / "Vendor.fun").write_text(
            "FUNCTION_BLOCK MpAxisBasic\nEND_FUNCTION_BLOCK\n", encoding="utf-8"
        )
        self.database = root / "index.sqlite3"
        self.index = CodeSearchIndex(self.database)
        self.result = self.index.rebuild(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_index_status_and_project_metadata(self) -> None:
        self.assertTrue(self.result["ok"])
        status = self.index.status()
        self.assertEqual(1, status["projects"])
        self.assertGreaterEqual(status["documents"], 6)
        overview = self.index.project_overview("ProjectA")
        self.assertEqual("4.12.5.95 SP", overview["as_version"])
        self.assertEqual("5.24.1", overview["metadata"]["technology_packages"]["mapp"])

    def test_search_and_origin_filter(self) -> None:
        result = self.index.search("MpAxisBasic", origin="user")
        self.assertEqual(1, result["count"])
        self.assertEqual("DemoProgram", result["results"][0]["symbol"])
        library = self.index.search("MpAxisBasic", origin="library")
        self.assertEqual("library", library["results"][0]["origin"])

    def test_find_symbol_and_context(self) -> None:
        symbols = self.index.find_symbol("DemoProgram")
        self.assertEqual(1, symbols["count"])
        context = self.index.get_context(symbols["results"][0]["document_id"])
        paths = {item["path"] for item in context["related_context"]}
        self.assertIn("Logical/Control/Init.st", paths)
        self.assertIn("Logical/Control/Variables.var", paths)


if __name__ == "__main__":
    unittest.main()
