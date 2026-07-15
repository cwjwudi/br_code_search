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
                '<?xml version="1.0"?><Project Version="1.0" />', encoding="utf-8"
            )
            (logical / "Cyclic.st").write_text(
                "PROGRAM SearchMe\nValue := 42;\nEND_PROGRAM\n", encoding="utf-8"
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

            searched = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "br_search_code",
                        "arguments": {"query": "Value := 42", "origin": "user"},
                    },
                }
            )
            self.assertEqual(1, searched["result"]["structuredContent"]["count"])

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


if __name__ == "__main__":
    unittest.main()
