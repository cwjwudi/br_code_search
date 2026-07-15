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

    subparsers.add_parser("qdrant-status", help="Check optional Qdrant client availability")

    toolchain_status = subparsers.add_parser("toolchain-status", help="Inspect the external B&R toolchain adapter without executing PLC commands")
    toolchain_status.add_argument("--root", help="br_device_autodev root override")

    import_report = subparsers.add_parser("import-toolchain-report", help="Import a br-plc-toolchain JSON report into build history")
    import_report.add_argument("report")
    import_report.add_argument("--project")
    import_report.add_argument("--source", default="br-plc-toolchain")
    import_report.add_argument("--as-version")
    import_report.add_argument("--ar-version")
    import_report.add_argument("--cpu-model")

    qdrant_export = subparsers.add_parser("qdrant-export", help="Export cached vectors and metadata to Qdrant")
    qdrant_export.add_argument("--path", help="Local Qdrant path (defaults to var/qdrant)")
    qdrant_export.add_argument("--url", help="Remote Qdrant URL")
    qdrant_export.add_argument("--collection", default="br_code_search")
    qdrant_export.add_argument("--backend", choices=["hashing", "sentence_transformers", "auto"], default="hashing")
    qdrant_export.add_argument("--model")
    qdrant_export.add_argument("--dimension", type=int, default=256)
    qdrant_export.add_argument("--project")
    qdrant_export.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    qdrant_export.add_argument("--language")
    qdrant_export.add_argument("--max-documents", type=int, default=50000)
    qdrant_export.add_argument("--batch-size", type=int, default=256)
    qdrant_export.add_argument("--recreate", action="store_true")

    qdrant_search = subparsers.add_parser("qdrant-search", help="Query a Qdrant semantic collection and hydrate SQLite source")
    qdrant_search.add_argument("query")
    qdrant_search.add_argument("--path", help="Local Qdrant path")
    qdrant_search.add_argument("--url", help="Remote Qdrant URL")
    qdrant_search.add_argument("--collection", default="br_code_search")
    qdrant_search.add_argument("--backend", choices=["hashing", "sentence_transformers", "auto"], default="hashing")
    qdrant_search.add_argument("--model")
    qdrant_search.add_argument("--dimension", type=int, default=256)
    qdrant_search.add_argument("--project")
    qdrant_search.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    qdrant_search.add_argument("--language")
    qdrant_search.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    qdrant_search.add_argument("--verified-only", action="store_true")
    qdrant_search.add_argument("--include-deprecated", action="store_true")
    qdrant_search.add_argument("--limit", type=int, default=10)
    qdrant_search.add_argument("--no-source", action="store_true")
    qdrant_search.add_argument("--max-chars-per-result", type=int, default=4000)
    qdrant_search.add_argument("--score-threshold", type=float)

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

    similar_fb = subparsers.add_parser("similar-function-block", help="Find similar FUNCTION_BLOCK implementations")
    similar_fb.add_argument("query", nargs="?")
    similar_fb.add_argument("--reference-document-id", type=int)
    similar_fb.add_argument("--project")
    similar_fb.add_argument("--quality", choices=["gold", "normal", "deprecated"])
    similar_fb.add_argument("--verified-only", action="store_true")
    similar_fb.add_argument("--include-deprecated", action="store_true")
    similar_fb.add_argument("--limit", type=int, default=10)
    similar_fb.add_argument("--no-source", action="store_true")

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

    architecture = subparsers.add_parser("architecture", help="Show one project's architecture summary")
    architecture.add_argument("project")

    library_usage = subparsers.add_parser("library-usage", help="Find projects and source units using a library")
    library_usage.add_argument("library")
    library_usage.add_argument("--project")
    library_usage.add_argument("--limit", type=int, default=100)

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

    compare = subparsers.add_parser("compare", help="Compare two indexed source units")
    compare.add_argument("left_document_id", type=int)
    compare.add_argument("right_document_id", type=int)
    compare.add_argument("--max-chars", type=int, default=30000)

    annotate = subparsers.add_parser("annotate-project", help="Persist project quality metadata")
    annotate.add_argument("project")
    annotate.add_argument("--quality", choices=["gold", "normal", "deprecated"], default="normal")
    annotate.add_argument("--verified", action="store_true")
    annotate.add_argument("--deprecated", action="store_true")
    annotate.add_argument("--do-not-copy", action="store_true")
    annotate.add_argument("--notes", default="")

    validation = subparsers.add_parser("record-validation", help="Record external build/field/compatibility feedback")
    validation.add_argument("project")
    validation.add_argument("--kind", choices=["build", "field", "compatibility"], required=True)
    validation.add_argument("--status", choices=["passed", "failed", "unknown"], required=True)
    validation.add_argument("--source", default="external")
    validation.add_argument("--as-version")
    validation.add_argument("--ar-version")
    validation.add_argument("--cpu-model")
    validation.add_argument("--artifact")
    validation.add_argument("--notes", default="")
    validation.add_argument("--error", dest="errors", action="append", default=[])
    validation.add_argument("--warning", dest="warnings", action="append", default=[])
    validation.add_argument("--tool")
    validation.add_argument("--target")
    validation.add_argument("--config")
    validation.add_argument("--report-path")
    validation.add_argument("--report-schema-version")
    validation.add_argument("--report-id")
    validation.add_argument("--log-path", dest="log_paths", action="append", default=[])
    validation.add_argument("--next-action", dest="next_actions", action="append", default=[])

    compile_history = subparsers.add_parser("compile-history", help="Show recorded build history for a project")
    compile_history.add_argument("project")
    compile_history.add_argument("--status", choices=["passed", "failed", "unknown"])
    compile_history.add_argument("--as-version")
    compile_history.add_argument("--ar-version")
    compile_history.add_argument("--cpu-model")
    compile_history.add_argument("--target")
    compile_history.add_argument("--tool")
    compile_history.add_argument("--limit", type=int, default=50)

    build_errors = subparsers.add_parser("search-build-errors", help="Search recorded build errors and warnings")
    build_errors.add_argument("query")
    build_errors.add_argument("--project")
    build_errors.add_argument("--limit", type=int, default=100)

    diagnostic_summary = subparsers.add_parser("build-diagnostic-summary", help="Aggregate imported build diagnostics")
    diagnostic_summary.add_argument("--project")
    diagnostic_summary.add_argument("--status", choices=["passed", "failed", "unknown"])
    diagnostic_summary.add_argument("--query")
    diagnostic_summary.add_argument("--limit", type=int, default=20)

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
        elif args.command == "qdrant-status":
            result = index.qdrant_status()
        elif args.command == "toolchain-status":
            result = index.toolchain_status(args.root)
        elif args.command == "import-toolchain-report":
            result = index.import_toolchain_report(
                args.report,
                project=args.project,
                source=args.source,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
            )
        elif args.command == "qdrant-export":
            result = index.export_qdrant(
                path=args.path,
                url=args.url,
                collection=args.collection,
                backend=args.backend,
                model=args.model,
                dimension=args.dimension,
                project=args.project,
                origin=args.origin,
                language=args.language,
                max_documents=args.max_documents,
                batch_size=args.batch_size,
                recreate=args.recreate,
            )
        elif args.command == "qdrant-search":
            result = index.search_qdrant(
                args.query,
                path=args.path,
                url=args.url,
                collection=args.collection,
                backend=args.backend,
                model=args.model,
                dimension=args.dimension,
                project=args.project,
                origin=args.origin,
                language=args.language,
                quality=args.quality,
                verified_only=args.verified_only,
                include_deprecated=args.include_deprecated,
                limit=args.limit,
                include_source=not args.no_source,
                max_chars_per_result=args.max_chars_per_result,
                score_threshold=args.score_threshold,
            )
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
        elif args.command == "similar-function-block":
            result = index.find_similar_function_block(
                args.query,
                reference_document_id=args.reference_document_id,
                project=args.project,
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
        elif args.command == "architecture":
            result = index.get_project_architecture(args.project)
        elif args.command == "library-usage":
            result = index.get_library_usage(args.library, project=args.project, limit=args.limit)
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
        elif args.command == "compare":
            result = index.compare_implementations(
                args.left_document_id, args.right_document_id, max_chars=args.max_chars
            )
        elif args.command == "evaluate":
            result = evaluate_dataset(index, args.dataset, top_k=args.top_k, max_cases=args.max_cases)
        elif args.command == "record-validation":
            result = index.record_project_validation(
                args.project,
                kind=args.kind,
                status=args.status,
                source=args.source,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                artifact=args.artifact,
                notes=args.notes,
                errors=args.errors,
                warnings=args.warnings,
                tool=args.tool,
                target=args.target,
                config=args.config,
                report_path=args.report_path,
                report_schema_version=args.report_schema_version,
                report_id=args.report_id,
                log_paths=args.log_paths,
                next_actions=args.next_actions,
            )
        elif args.command == "compile-history":
            result = index.get_compile_history(
                args.project,
                status=args.status,
                as_version=args.as_version,
                ar_version=args.ar_version,
                cpu_model=args.cpu_model,
                target=args.target,
                tool=args.tool,
                limit=args.limit,
            )
        elif args.command == "search-build-errors":
            result = index.search_build_errors(args.query, project=args.project, limit=args.limit)
        elif args.command == "build-diagnostic-summary":
            result = index.get_build_diagnostic_summary(
                project=args.project,
                status=args.status,
                query=args.query,
                limit=args.limit,
            )
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
