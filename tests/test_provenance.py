from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from br_code_search.core import CodeSearchIndex
from br_code_search.provenance import inspect_git


class ProvenanceTests(unittest.TestCase):
    def test_plain_source_directory_is_explicitly_unversioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_git(Path(directory))
            self.assertTrue(result["ok"])
            self.assertFalse(result["available"])
            self.assertIsNone(result["revision"])

    def test_current_repository_has_read_only_git_revision(self) -> None:
        result = inspect_git(Path(__file__).resolve().parents[1])
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertTrue(result["revision"])
        self.assertIn("origin", result["remotes"])

    def test_index_source_provenance_uses_recorded_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "index.sqlite3"
            index = CodeSearchIndex(database)
            result = index.source_provenance()
            self.assertFalse(result["ok"])
            self.assertIn("database", result["error"])

