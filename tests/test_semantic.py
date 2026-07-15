from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
