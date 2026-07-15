from __future__ import annotations

import unittest
from pathlib import Path

from br_code_search.core import CodeSearchIndex
from br_code_search.semantic import (
    HashEmbeddingBackend,
    create_embedding_backend,
    register_embedding_backend,
)


class SemanticBackendTests(unittest.TestCase):
    def test_offline_backend_is_normalized_and_registerable(self) -> None:
        backend = create_embedding_backend("hashing", dimension=64)
        self.assertEqual(64, backend.dimension)
        vectors = backend.encode(["fault restart", "warning reset"])
        self.assertEqual(2, len(vectors))
        self.assertEqual(64, len(vectors[0]))

        register_embedding_backend("test_backend", lambda _model, _dimension: HashEmbeddingBackend(32))
        custom = create_embedding_backend("test_backend")
        self.assertEqual("hashing:32", custom.key)

    def test_backend_status_does_not_load_model(self) -> None:
        status = CodeSearchIndex(Path("status.sqlite3")).embedding_status("hashing", dimension=64)
        self.assertTrue(status["available"])
        self.assertEqual("offline_hashing_fallback", status["backend_kind"])
        optional = CodeSearchIndex(Path("status.sqlite3")).embedding_status("sentence_transformers")
        self.assertIn("available", optional)


if __name__ == "__main__":
    unittest.main()
