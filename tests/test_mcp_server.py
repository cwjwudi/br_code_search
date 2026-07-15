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


if __name__ == "__main__":
    unittest.main()
