from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from br_code_search.core import (
    CodeSearchIndex,
    classify_reference_access,
    parse_declarations,
    parse_software_tasks,
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

    def test_software_task_parser(self) -> None:
        text = """<Software><TaskClass Name=\"Cyclic#1\"><Task Name=\"Main\" Source=\"Control.Cyclic.prg\" CycleTimeUs=\"4000\" /></TaskClass></Software>"""
        tasks = parse_software_tasks(text, "Cpu.sw")
        self.assertEqual(1, len(tasks))
        self.assertEqual("Cyclic#1", tasks[0]["task_class"])
        self.assertEqual(4000, tasks[0]["cycle_time_us"])

    def test_variable_declaration_parser(self) -> None:
        declarations = parse_declarations(
            "VAR\n fbAxis : MpAxisBasic;\n values : ARRAY[1..2] OF DemoType;\nEND_VAR\n"
        )
        self.assertEqual(["fbAxis", "values"], [item["name"] for item in declarations])
        self.assertEqual("MpAxisBasic", declarations[0]["type_name"])
        self.assertEqual("DemoType", declarations[1]["type_name"])

    def test_reference_access_classifier(self) -> None:
        self.assertEqual("write", classify_reference_access("Axis", "Axis.Enable := TRUE;"))
        self.assertEqual("call", classify_reference_access("Axis", "Axis();"))
        self.assertEqual("member", classify_reference_access("Axis", "IF Axis.Enable THEN"))
        self.assertEqual("read", classify_reference_access("Axis", "Ready := Axis;"))

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
        (self.project / "Physical" / "Cpu.pkg").parent.mkdir(parents=True, exist_ok=True)
        (self.project / "Physical" / "Cpu.pkg").write_text(
            """<Cpu><Configuration ModuleId=\"X20CP1585\"><AutomationRuntime Version=\"H4.93\" /></Configuration></Cpu>""",
            encoding="utf-8",
        )
        (module / "Cyclic.st").write_text(PROGRAM, encoding="utf-8")
        (module / "Init.st").write_text(
            "PROGRAM _INIT\nReady := TRUE;\nEND_PROGRAM\n", encoding="utf-8"
        )
        (module / "Variables.var").write_text(
            "VAR\n Ready : BOOL;\n Demo : DemoType;\nEND_VAR\n", encoding="utf-8"
        )
        (module / "Types.typ").write_text(
            "TYPE\nDemoType : STRUCT\n Value : INT;\nEND_STRUCT;\nEND_TYPE\n", encoding="utf-8"
        )
        (self.project / "Cpu.sw").write_text(
            """<Software><TaskClass Name=\"Cyclic#1\"><Task Name=\"Main\" Source=\"Control.Cyclic.prg\" CycleTimeUs=\"4000\" /></TaskClass></Software>""",
            encoding="utf-8",
        )
        (self.project / "Cpu.pkg").write_text(
            """<Cpu><Configuration ModuleId=\"X20CP1585\"><AutomationRuntime Version=\"H4.93\" /></Configuration></Cpu>""",
            encoding="utf-8",
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
        self.assertEqual(["H4.93"], overview["ar_versions"])
        self.assertEqual(["X20CP1585"], overview["cpu_models"])

    def test_search_and_origin_filter(self) -> None:
        result = self.index.search("MpAxisBasic", origin="user")
        self.assertEqual(1, result["count"])
        self.assertEqual("DemoProgram", result["results"][0]["symbol"])
        library = self.index.search("MpAxisBasic", origin="library")
        self.assertEqual("library", library["results"][0]["origin"])
        filtered = self.index.search(
            "DemoProgram",
            as_version="4.12",
            ar_version="H4.93",
            cpu_model="X20CP1585",
            library="mapp",
            library_version="5.24.1",
        )
        self.assertEqual(1, filtered["count"])
        unit_results = self.index.search("Ready", limit=10)
        file_results = self.index.search("Ready", limit=10, aggregate_files=True)
        self.assertLessEqual(file_results["count"], unit_results["count"])
        self.assertTrue(any(item["symbol_count"] > 1 for item in file_results["results"]))
        self.assertTrue(all(item["aggregation"] == "file" for item in file_results["results"]))

    def test_find_symbol_and_context(self) -> None:
        symbols = self.index.find_symbol("DemoProgram")
        self.assertEqual(1, symbols["count"])
        context = self.index.get_context(symbols["results"][0]["document_id"])
        paths = {item["path"] for item in context["related_context"]}
        self.assertIn("Logical/Control/Init.st", paths)
        self.assertIn("Logical/Control/Variables.var", paths)
        self.assertEqual("Main", context["tasks"][0]["task_name"])
        self.assertIn("Demo", {item["name"] for item in context["declarations"]})
        demo_type = next(item for item in context["type_references"] if item["type_name"] == "DemoType")
        self.assertTrue(demo_type["resolved"])

    def test_tasks_types_and_references(self) -> None:
        tasks = self.index.get_task_configuration("ProjectA")
        self.assertEqual(1, tasks["count"])
        self.assertEqual(4000, tasks["tasks"][0]["cycle_time_us"])
        self.assertEqual("X20CP1585", tasks["tasks"][0]["cpu_model"])
        self.assertEqual("H4.93", tasks["tasks"][0]["automation_runtime_version"])
        filtered_tasks = self.index.get_task_configuration("ProjectA", cpu_model="X20CP1585", ar_version="H4.93")
        self.assertEqual(1, filtered_tasks["count"])
        definition = self.index.get_type_definition("DemoType")
        self.assertEqual(1, definition["count"])
        references = self.index.find_references("Ready")
        self.assertGreaterEqual(references["count"], 2)
        self.assertIn("declaration", {item["relation"] for item in references["references"]})
        self.assertEqual(
            references["count"],
            len({(item["path"], item["line"], item["relation"]) for item in references["references"]}),
        )
        axis_refs = self.index.find_references("Axis")
        self.assertIn("write", {item["access"] for item in axis_refs["references"]})
        self.assertIn("call", {item["access"] for item in axis_refs["references"]})

    def test_incremental_sync_and_similar_search(self) -> None:
        first = self.index.sync(self.source)
        self.assertEqual("sync", first["mode"])
        self.assertGreaterEqual(first["skipped_files"], 6)
        cyclic = self.project / "Logical" / "Control" / "Cyclic.st"
        cyclic.write_text(PROGRAM + "\n// added reference pattern\n", encoding="utf-8")
        changed = self.index.sync(self.source)
        self.assertEqual(1, changed["changed_files"])
        self.assertGreaterEqual(changed["skipped_files"], 5)
        symbols = self.index.find_symbol("DemoProgram")
        similar = self.index.search_similar(reference_document_id=symbols["results"][0]["document_id"], limit=3)
        self.assertEqual("lexical_structural", similar["mode"])
        self.assertLessEqual(similar["count"], 3)

    def test_target_aware_filters_for_multi_target_project(self) -> None:
        project = self.source / "MultiTarget"
        (project / "Logical" / "ModuleA").mkdir(parents=True)
        (project / "Logical" / "ModuleB").mkdir(parents=True)
        (project / "MultiTarget.apj").write_text(
            '<?xml version="1.0"?><?AutomationStudio Version="6.0"?><Project Version="1.0" />',
            encoding="utf-8",
        )
        for name, model, ar, module in (
            ("TargetA", "X20CP1000", "A4.10", "ModuleA"),
            ("TargetB", "X20CP2000", "B4.10", "ModuleB"),
        ):
            target = project / "Physical" / name
            target.mkdir(parents=True)
            (target / "Cpu.pkg").write_text(
                f'<Cpu><Configuration ModuleId="{model}"><AutomationRuntime Version="{ar}" /></Configuration></Cpu>',
                encoding="utf-8",
            )
            (target / "Cpu.sw").write_text(
                f'<Software><TaskClass Name="Cyclic"><Task Name="{name}" Source="{module}.Program.prg" /></TaskClass></Software>',
                encoding="utf-8",
            )
            (project / "Logical" / module / "Program.st").write_text(
                f"PROGRAM {name}Program\nTargetOnly{name} := TRUE;\nEND_PROGRAM\n", encoding="utf-8"
            )
        self.index.rebuild(self.source)
        only_a = self.index.search("TargetOnlyTargetA", project="MultiTarget", cpu_model="X20CP1000")
        self.assertEqual(1, only_a["count"])
        self.assertEqual(["X20CP1000"], only_a["results"][0]["target_cpu_models"])
        self.assertEqual(0, self.index.search("TargetOnlyTargetA", project="MultiTarget", cpu_model="X20CP2000")["count"])
        only_b = self.index.find_symbol("TargetBProgram", project="MultiTarget", cpu_model="X20CP2000")
        self.assertEqual(1, only_b["count"])
        self.assertEqual(["B4.10"], only_b["results"][0]["target_ar_versions"])

    def test_hybrid_search_and_embedding_cache(self) -> None:
        first = self.index.search_hybrid("axis ready", backend="hashing", limit=3, include_source=False)
        self.assertEqual("hybrid", first["mode"])
        self.assertEqual("offline_hashing_fallback", first["backend_kind"])
        self.assertGreater(first["embedding_documents_encoded"], 0)
        second = self.index.search_hybrid("axis ready", backend="hashing", limit=3, include_source=False)
        self.assertGreater(second["embedding_cache_hits"], 0)
        self.assertEqual(0, second["embedding_documents_encoded"])
        self.assertTrue(all("hybrid_score" in item for item in second["results"]))

    def test_project_annotations_filter_results(self) -> None:
        annotation = self.index.annotate_project(
            "ProjectA", quality="gold", verified=True, notes="现场验证通过"
        )
        self.assertTrue(annotation["verified"])
        self.assertTrue(self.index.project_metadata_path.exists())
        filtered = self.index.search("DemoProgram", quality="gold", verified_only=True)
        self.assertEqual(1, filtered["count"])
        self.assertEqual("gold", filtered["results"][0]["quality"])
        self.assertTrue(filtered["results"][0]["verified"])
        self.index.rebuild(self.source)
        overview = self.index.project_overview("ProjectA")
        self.assertEqual("gold", overview["quality"])
        self.assertTrue(overview["verified"])
        self.index.annotate_project("ProjectA", quality="deprecated", do_not_copy=True)
        self.assertEqual(0, self.index.search("DemoProgram")["count"])
        self.assertEqual(1, self.index.search("DemoProgram", quality="deprecated")["count"])

    def test_project_validation_feedback_is_persisted_and_returned(self) -> None:
        recorded = self.index.record_project_validation(
            "ProjectA",
            kind="build",
            status="passed",
            source="automation-studio",
            as_version="4.12.5.95 SP",
            ar_version="H4.93",
            cpu_model="X20CP1585",
            artifact="build-report.xml",
        )
        self.assertEqual("passed", recorded["record"]["status"])
        self.assertEqual(1, recorded["validation"]["record_count"])
        overview = self.index.project_overview("ProjectA")
        self.assertEqual("passed", overview["validation"]["latest_by_kind"]["build"]["status"])
        search = self.index.search("DemoProgram")
        self.assertEqual("passed", search["results"][0]["validation"]["latest_by_kind"]["build"]["status"])
        hybrid = self.index.search_hybrid("DemoProgram", backend="hashing", limit=1, include_source=False)
        self.assertEqual(0.04, hybrid["results"][0]["validation_boost"])
        self.index.rebuild(self.source)
        self.assertEqual(1, self.index.project_overview("ProjectA")["validation"]["record_count"])


if __name__ == "__main__":
    unittest.main()
