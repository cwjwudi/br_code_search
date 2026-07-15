from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .core import CodeSearchIndex


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

    search = subparsers.add_parser("search", help="Search indexed code")
    search.add_argument("query")
    search.add_argument("--project")
    search.add_argument("--origin", choices=["all", "user", "library", "physical"], default="all")
    search.add_argument("--language")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--no-source", action="store_true")

    symbol = subparsers.add_parser("find-symbol", help="Find symbols by exact name or prefix")
    symbol.add_argument("name")
    symbol.add_argument("--project")
    symbol.add_argument("--type", dest="symbol_type")
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
    similar.add_argument("--limit", type=int, default=10)
    similar.add_argument("--no-source", action="store_true")

    overview = subparsers.add_parser("overview", help="Show one project's indexed structure")
    overview.add_argument("project")
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
        elif args.command == "search":
            result = index.search(
                args.query,
                project=args.project,
                origin=args.origin,
                language=args.language,
                limit=args.limit,
                include_source=not args.no_source,
            )
        elif args.command == "find-symbol":
            result = index.find_symbol(
                args.name,
                project=args.project,
                symbol_type=args.symbol_type,
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
                limit=args.limit,
                include_source=not args.no_source,
            )
        else:
            result = index.project_overview(args.project)
    except (OSError, ValueError) as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1
    print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
