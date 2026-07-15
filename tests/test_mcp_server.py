from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from br_code_search.core import CodeSearchIndex
from br_code_search.mcp_server import McpServer, TOOL_DEFINITIONS


class McpServerTests(unittest.TestCase):
    def test_contract_and_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Source" / "Sample"
            logical = source / "Logical"
            logical.mkdir(parents=True)
            (source / "Sample.apj").write_text(
                '<?xml version="1.0"?><?AutomationStudio Version="4.12"?><Project Version="1.0" />', encoding="utf-8"
            )
            (source / "Cpu.pkg").write_text(
                '<Cpu><Configuration ModuleId="X20CP1585"><AutomationRuntime Version="H4.93" /></Configuration></Cpu>',
                encoding="utf-8",
            )
            (logical / "Cyclic.st").write_text(
                "PROGRAM SearchMe\nValue := 42;\nEND_PROGRAM\n", encoding="utf-8"
            )
            (logical / "Types.typ").write_text(
                "TYPE\nDemoType : STRUCT\n Value : INT;\nEND_STRUCT;\nEND_TYPE\n", encoding="utf-8"
            )
            (source / "Cpu.sw").write_text(
                "<Software><TaskClass Name=\"Cyclic#1\"><Task Name=\"Main\" Source=\"Logical.Cyclic.prg\" /></TaskClass></Software>",
                encoding="utf-8",
            )
            server = McpServer(CodeSearchIndex(root / "index.sqlite3"), str(root / "Source"))

            initialize = server.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
            self.assertEqual("br-code-search", initialize["result"]["serverInfo"]["name"])

            listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(len(TOOL_DEFINITIONS), len(listed["result"]["tools"]))

            indexed = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "br_index_codebase", "arguments": {}},
                }
            )
            self.assertFalse(indexed["result"]["isError"])

            library_usage = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 34,
                    "method": "tools/call",
                    "params": {"name": "br_get_library_usage", "arguments": {"library": "SearchMe"}},
                }
            )
            self.assertFalse(library_usage["result"]["isError"])
            self.assertEqual(1, library_usage["result"]["structuredContent"]["count"])

            architecture = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 35,
                    "method": "tools/call",
                    "params": {"name": "br_get_project_architecture", "arguments": {"project": "Sample"}},
                }
            )
            self.assertFalse(architecture["result"]["isError"])
            self.assertEqual("Sample", architecture["result"]["structuredContent"]["project"])

            similar_fb = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 36,
                    "method": "tools/call",
                    "params": {
                        "name": "br_find_similar_function_block",
                        "arguments": {"query": "DemoType", "include_source": False},
                    },
                }
            )
            self.assertFalse(similar_fb["result"]["isError"])
            self.assertEqual("function_block_similarity", similar_fb["result"]["structuredContent"]["mode"])

            embedding_status = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 30,
                    "method": "tools/call",
                    "params": {"name": "br_get_embedding_status", "arguments": {"backend": "hashing"}},
                }
            )
            self.assertFalse(embedding_status["result"]["isError"])
            self.assertTrue(embedding_status["result"]["structuredContent"]["available"])

            qdrant_status = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 40,
                    "method": "tools/call",
                    "params": {"name": "br_get_qdrant_status", "arguments": {}},
                }
            )
            self.assertFalse(qdrant_status["result"]["isError"])
            self.assertIn("available", qdrant_status["result"]["structuredContent"])
            if qdrant_status["result"]["structuredContent"]["available"]:
                qdrant_export = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 44,
                        "method": "tools/call",
                        "params": {
                            "name": "br_export_qdrant",
                            "arguments": {
                                "path": str(root / "qdrant"),
                                "collection": "mcp-test",
                                "backend": "hashing",
                                "max_documents": 100,
                                "recreate": True,
                            },
                        },
                    }
                )
                self.assertFalse(qdrant_export["result"]["isError"])
                qdrant_search = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 45,
                        "method": "tools/call",
                        "params": {
                            "name": "br_search_qdrant",
                            "arguments": {
                                "query": "SearchMe",
                                "path": str(root / "qdrant"),
                                "collection": "mcp-test",
                                "limit": 2,
                                "include_source": False,
                            },
                        },
                    }
                )
                self.assertFalse(qdrant_search["result"]["isError"])
                self.assertEqual("qdrant", qdrant_search["result"]["structuredContent"]["mode"])

            toolchain_status = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {
                        "name": "br_get_toolchain_status",
                        "arguments": {"root": str(root / "missing-toolchain")},
                    },
                }
            )
            self.assertFalse(toolchain_status["result"]["isError"])
            self.assertTrue(toolchain_status["result"]["structuredContent"]["read_only"])

            provenance = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 46,
                    "method": "tools/call",
                    "params": {"name": "br_get_source_provenance", "arguments": {"root": str(source)}},
                }
            )
            self.assertFalse(provenance["result"]["isError"])
            self.assertFalse(provenance["result"]["structuredContent"]["available"])

            impact = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 47,
                    "method": "tools/call",
                    "params": {"name": "br_get_symbol_impact", "arguments": {"name": "SearchMe"}},
                }
            )
            self.assertFalse(impact["result"]["isError"])
            self.assertIn("access_counts", impact["result"]["structuredContent"])

            dataset = root / "queries.json"
            dataset.write_text(
                '{"version": 1, "queries": [{"id": "search-me", "operation": "search", '
                '"query": "SearchMe", "relevant": [{"path": "Logical/Cyclic.st", "symbol": "SearchMe"}]}]}',
                encoding="utf-8",
            )
            evaluated = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 31,
                    "method": "tools/call",
                    "params": {
                        "name": "br_evaluate_retrieval",
                        "arguments": {"dataset_path": str(dataset), "top_k": 3},
                    },
                }
            )
            self.assertFalse(evaluated["result"]["isError"])
            self.assertEqual(1.0, evaluated["result"]["structuredContent"]["mrr"])

            hybrid = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 32,
                    "method": "tools/call",
                    "params": {
                        "name": "br_search_hybrid",
                        "arguments": {"query": "SearchMe", "backend": "hashing", "limit": 2, "include_source": False},
                    },
                }
            )
            self.assertFalse(hybrid["result"]["isError"])
            self.assertEqual("hybrid", hybrid["result"]["structuredContent"]["mode"])

            searched = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "br_search_code",
                        "arguments": {
                            "query": "Value := 42",
                            "origin": "user",
                            "ar_version": "H4.93",
                            "cpu_model": "X20CP1585",
                            "aggregate_files": True,
                        },
                    },
                }
            )
            self.assertGreaterEqual(searched["result"]["structuredContent"]["count"], 1)
            self.assertTrue(
                all(item["aggregation"] == "file" for item in searched["result"]["structuredContent"]["results"])
            )

            similar = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "br_find_similar_code",
                        "arguments": {"query": "SearchMe := 42", "limit": 2, "include_source": False},
                    },
                }
            )
            self.assertFalse(similar["result"]["isError"])
            self.assertEqual("lexical_structural", similar["result"]["structuredContent"]["mode"])

            annotated = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "br_annotate_project",
                        "arguments": {"project": "Sample", "quality": "gold", "verified": True},
                    },
                }
            )
            self.assertFalse(annotated["result"]["isError"])
            self.assertEqual("gold", annotated["result"]["structuredContent"]["quality"])

            validation = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 33,
                    "method": "tools/call",
                    "params": {
                        "name": "br_record_project_validation",
                        "arguments": {
                            "project": "Sample",
                            "kind": "build",
                            "status": "failed",
                            "errors": ["E_SAMPLE: failed compile"],
                        },
                    },
                }
            )
            self.assertFalse(validation["result"]["isError"])
            self.assertEqual("failed", validation["result"]["structuredContent"]["record"]["status"])

            report = root / "build-report.json"
            report.write_text(
                '{"ok": true, "tool": "plc_build_project", "summary": "Build passed", '
                '"data": {"ok": true, "as_version": "6.5.0", "warnings": ["W1"]}}',
                encoding="utf-8",
            )
            imported = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 42,
                    "method": "tools/call",
                    "params": {
                        "name": "br_import_toolchain_report",
                        "arguments": {"report_path": str(report), "project": "Sample"},
                    },
                }
            )
            self.assertFalse(imported["result"]["isError"])
            self.assertEqual("passed", imported["result"]["structuredContent"]["record"]["status"])

            diagnostic_summary = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 43,
                    "method": "tools/call",
                    "params": {
                        "name": "br_get_build_diagnostic_summary",
                        "arguments": {"project": "Sample"},
                    },
                }
            )
            self.assertFalse(diagnostic_summary["result"]["isError"])
            self.assertEqual(2, diagnostic_summary["result"]["structuredContent"]["record_count"])

            history = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 37,
                    "method": "tools/call",
                    "params": {"name": "br_get_compile_history", "arguments": {"project": "Sample"}},
                }
            )
            self.assertFalse(history["result"]["isError"])
            self.assertEqual(2, history["result"]["structuredContent"]["count"])

            build_errors = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 38,
                    "method": "tools/call",
                    "params": {
                        "name": "br_search_build_errors",
                        "arguments": {"query": "E_SAMPLE"},
                    },
                }
            )
            self.assertFalse(build_errors["result"]["isError"])
            self.assertEqual(1, build_errors["result"]["structuredContent"]["count"])

            tasks = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "br_get_task_configuration",
                        "arguments": {"project": "Sample", "cpu_model": "X20CP1585", "ar_version": "H4.93"},
                    },
                }
            )
            self.assertEqual(1, tasks["result"]["structuredContent"]["count"])
            self.assertEqual("X20CP1585", tasks["result"]["structuredContent"]["tasks"][0]["cpu_model"])

            type_result = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "br_get_type_definition", "arguments": {"type_name": "DemoType"}},
                }
            )
            self.assertEqual(1, type_result["result"]["structuredContent"]["count"])

            references = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "br_find_references", "arguments": {"name": "Value"}},
                }
            )
            self.assertGreaterEqual(references["result"]["structuredContent"]["count"], 1)

            comparison = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 39,
                    "method": "tools/call",
                    "params": {
                        "name": "br_compare_implementations",
                        "arguments": {"left_document_id": 1, "right_document_id": 1},
                    },
                }
            )
            self.assertFalse(comparison["result"]["isError"])
            self.assertTrue(comparison["result"]["structuredContent"]["same"])


if __name__ == "__main__":
    unittest.main()
