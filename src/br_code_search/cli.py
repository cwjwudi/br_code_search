from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .core import CodeSearchIndex
from .evaluation import evaluate_dataset


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "var" / "br_code_search.sqlite3"


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Index and search B&R Automation Studio source code")
    parser.add_argument(
        "--db",
        default=os.environ.get("BR_CODE_SEARCH_DB", str(DEFAULT_DB)),
        help="SQLite index path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Rebuild the code index")
    index.add_argument(
        "--source",
        default=os.environ.get("BR_CODE_SEARCH_SOURCE"),
        required=not bool(os.environ.get("BR_CODE_SEARCH_SOURCE")),
    )

    sync = subparsers.add_parser("sync", help="Incrementally synchronize the code index")
    sync.add_argument(
        "--source",
        default=os.environ.get("BR_CODE_SEARCH_SOURCE"),
        required=not bool(os.environ.get("BR_CODE_SEARCH_SOURCE")),
    )

    subparsers.add_parser("status", help="Show index status")

    embedding_status = subparsers.add_parser("embedding-status", help="Check local embedding backend availability")
    embedding_status.add_argument("--backend", choices=["hashing", "sentence_transformers", "auto"], default="hashing")
    embedding_status.add_argument("--model")
    embedding_status.add_argument("--dimension", type=int, default=256)

    search = subparsers.add_parser("search", help="Search indexed code")
    search.add_argument("query")
    search.add_argument("--project")
    search.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    search.add_argument("--language")
    search.add_argument("--as-version")
    search.add_argument("--ar-version")
    search.add_argument("--cpu-model")
    search.add_argument("--library")
    search.add_argument("--library-version")
    search.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    search.add_argument("--verified-only", action="store_true")
    search.add_argument("--include-deprecated", action="store_true")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--no-source", action="store_true")
    search.add_argument("--aggregate-files", action="store_true", help="Group matching units by source file")

    symbol = subparsers.add_parser("find-symbol", help="Find symbols by exact name or prefix")
    symbol.add_argument("name")
    symbol.add_argument("--project")
    symbol.add_argument("--type", dest="symbol_type")
    symbol.add_argument("--as-version")
    symbol.add_argument("--ar-version")
    symbol.add_argument("--cpu-model")
    symbol.add_argument("--library")
    symbol.add_argument("--library-version")
    symbol.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    symbol.add_argument("--verified-only", action="store_true")
    symbol.add_argument("--include-deprecated", action="store_true")
    symbol.add_argument("--limit", type=int, default=20)

    get = subparsers.add_parser("get-symbol", help="Get a source unit by document id")
    get.add_argument("document_id", type=int)
    get.add_argument("--max-chars", type=int, default=30000)

    context = subparsers.add_parser("context", help="Get a source unit and related module context")
    context.add_argument("document_id", type=int)
    context.add_argument("--max-chars", type=int, default=30000)

    similar = subparsers.add_parser("similar", help="Find lexical/structural neighbors")
    similar.add_argument("query", nargs="?")
    similar.add_argument("--reference-document-id", type=int)
    similar.add_argument("--project")
    similar.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    similar.add_argument("--language")
    similar.add_argument("--as-version")
    similar.add_argument("--ar-version")
    similar.add_argument("--cpu-model")
    similar.add_argument("--library")
    similar.add_argument("--library-version")
    similar.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    similar.add_argument("--verified-only", action="store_true")
    similar.add_argument("--include-deprecated", action="store_true")
    similar.add_argument("--limit", type=int, default=10)
    similar.add_argument("--no-source", action="store_true")

    hybrid = subparsers.add_parser("hybrid", help="Hybrid lexical/structural/vector retrieval")
    hybrid.add_argument("query")
    hybrid.add_argument("--project")
    hybrid.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    hybrid.add_argument("--language")
    hybrid.add_argument("--as-version")
    hybrid.add_argument("--ar-version")
    hybrid.add_argument("--cpu-model")
    hybrid.add_argument("--library")
    hybrid.add_argument("--library-version")
    hybrid.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    hybrid.add_argument("--verified-only", action="store_true")
    hybrid.add_argument("--include-deprecated", action="store_true")
    hybrid.add_argument("--limit", type=int, default=10)
    hybrid.add_argument("--no-source", action="store_true")
    hybrid.add_argument("--aggregate-files", action="store_true")
    hybrid.add_argument("--backend", choices=["hashing", "sentence_transformers", "auto"], default="hashing")
    hybrid.add_argument("--model", help="Local SentenceTransformers model name/path")
    hybrid.add_argument("--dimension", type=int, default=256)
    hybrid.add_argument("--semantic-weight", type=float, default=0.5)
    hybrid.add_argument("--lexical-weight", type=float, default=0.35)
    hybrid.add_argument("--structural-weight", type=float, default=0.15)
    hybrid.add_argument("--max-documents", type=int, default=50000)

    overview = subparsers.add_parser("overview", help="Show one project's indexed structure")
    overview.add_argument("project")

    tasks = subparsers.add_parser("tasks", help="Show B&R TaskClass/Task assignments")
    tasks.add_argument("project")
    tasks.add_argument("--task-name")
    tasks.add_argument("--source")
    tasks.add_argument("--cpu-model")
    tasks.add_argument("--ar-version")

    type_definition = subparsers.add_parser("type", help="Get a TYPE declaration")
    type_definition.add_argument("type_name")
    type_definition.add_argument("--project")

    references = subparsers.add_parser("references", help="Find whole-identifier references")
    references.add_argument("name")
    references.add_argument("--project")
    references.add_argument("--limit", type=int, default=100)

    annotate = subparsers.add_parser("annotate-project", help="Persist project quality metadata")
    annotate.add_argument("project")
    annotate.add_argument("--quality", choices=["gold", "normal", "deprecated"], default="normal")
    annotate.add_argument("--verified", action="store_true")
    annotate.add_argument("--deprecated", action="store_true")
    annotate.add_argument("--do-not-copy", action="store_true")
    annotate.add_argument("--notes", default="")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate retrieval quality against a versioned JSON dataset")
    evaluate.add_argument(
        "dataset",
        nargs="?",
        default=str(REPO_ROOT / "eval" / "retrieval_queries.json"),
        help="Evaluation JSON path (defaults to the bundled example dataset)",
    )
    evaluate.add_argument("--top-k", type=int, default=5)
    evaluate.add_argument("--max-cases", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    index = CodeSearchIndex(args.db)
    try:
        if args.command == "index":
            result = index.rebuild(args.source)
        elif args.command == "sync":
            result = index.sync(args.source)
        elif args.command == "status":
            result = index.status()
        elif args.command == "embedding-status":
            result = index.embedding_status(args.backend, model=args.model, dimension=args.dimension)
        elif args.command == "search":
            result = index.search(
                args.query,
                project=args.project,
                origin=args.origin,
                language=args.language,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                library=args.library,
                library_version=args.library_version,
                quality=args.quality,
                verified_only=args.verified_only,
                include_deprecated=args.include_deprecated,
                limit=args.limit,
                include_source=not args.no_source,
                aggregate_files=args.aggregate_files,
            )
        elif args.command == "find-symbol":
            result = index.find_symbol(
                args.name,
                project=args.project,
                symbol_type=args.symbol_type,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                library=args.library,
                library_version=args.library_version,
                quality=args.quality,
                verified_only=args.verified_only,
                include_deprecated=args.include_deprecated,
                limit=args.limit,
            )
        elif args.command == "get-symbol":
            result = index.get_symbol(args.document_id, max_chars=args.max_chars)
        elif args.command == "context":
            result = index.get_context(args.document_id, max_chars=args.max_chars)
        elif args.command == "similar":
            result = index.search_similar(
                args.query,
                reference_document_id=args.reference_document_id,
                project=args.project,
                origin=args.origin,
                language=args.language,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                library=args.library,
                library_version=args.library_version,
                quality=args.quality,
                verified_only=args.verified_only,
                include_deprecated=args.include_deprecated,
                limit=args.limit,
                include_source=not args.no_source,
            )
        elif args.command == "hybrid":
            result = index.search_hybrid(
                args.query,
                project=args.project,
                origin=args.origin,
                language=args.language,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                library=args.library,
                library_version=args.library_version,
                quality=args.quality,
                verified_only=args.verified_only,
                include_deprecated=args.include_deprecated,
                limit=args.limit,
                include_source=not args.no_source,
                aggregate_files=args.aggregate_files,
                backend=args.backend,
                model=args.model,
                dimension=args.dimension,
                semantic_weight=args.semantic_weight,
                lexical_weight=args.lexical_weight,
                structural_weight=args.structural_weight,
                max_documents=args.max_documents,
            )
        elif args.command == "overview":
            result = index.project_overview(args.project)
        elif args.command == "tasks":
            result = index.get_task_configuration(
                args.project,
                task_name=args.task_name,
                source=args.source,
                cpu_model=args.cpu_model,
                ar_version=args.ar_version,
            )
        elif args.command == "type":
            result = index.get_type_definition(args.type_name, project=args.project)
        elif args.command == "references":
            result = index.find_references(args.name, project=args.project, limit=args.limit)
        elif args.command == "evaluate":
            result = evaluate_dataset(index, args.dataset, top_k=args.top_k, max_cases=args.max_cases)
        else:
            result = index.annotate_project(
                args.project,
                quality=args.quality,
                verified=args.verified,
                deprecated=args.deprecated,
                do_not_copy=args.do_not_copy,
                notes=args.notes,
            )
    except (OSError, ValueError) as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1
    print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
