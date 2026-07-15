from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from br_code_search.core import CodeSearchIndex
from br_code_search.evaluation import evaluate_dataset


class EvaluationTests(unittest.TestCase):
    def test_hit_at_k_and_mrr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Source" / "Sample"
            logical = source / "Logical"
            logical.mkdir(parents=True)
            (source / "Sample.apj").write_text(
                '<?xml version="1.0"?><?AutomationStudio Version="4.12"?><Project Version="1.0" />',
                encoding="utf-8",
            )
            (logical / "Main.st").write_text(
                "PROGRAM DemoProgram\nReady := TRUE;\nEND_PROGRAM\n", encoding="utf-8"
            )
            (logical / "Types.typ").write_text(
                "TYPE\nDemoType : STRUCT\n Value : INT;\nEND_STRUCT;\nEND_TYPE\n", encoding="utf-8"
            )
            index = CodeSearchIndex(root / "index.sqlite3")
            index.rebuild(root / "Source")
            dataset = root / "queries.json"
            dataset.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "queries": [
                            {
                                "id": "program-search",
                                "operation": "search",
                                "query": "DemoProgram",
                                "relevant": [{"path": "Logical/Main.st", "symbol": "DemoProgram"}],
                            },
                            {
                                "id": "type-symbol",
                                "operation": "find_symbol",
                                "query": "DemoType",
                                "relevant": [{"path": "Logical/Types.typ", "symbol": "DemoType"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = evaluate_dataset(index, dataset, top_k=3)
            self.assertTrue(result["ok"])
            self.assertEqual(2, result["query_count"])
            self.assertEqual(2, result["hit_at_k"]["1"]["hits"])
            self.assertEqual(1.0, result["mrr"])
            self.assertTrue(all(case["hit"] for case in result["cases"]))


if __name__ == "__main__":
    unittest.main()
