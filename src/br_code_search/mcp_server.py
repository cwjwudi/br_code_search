from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .core import CodeSearchIndex


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "var" / "br_code_search.sqlite3"
PROTOCOL_VERSION = "2025-06-18"


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "br_index_codebase",
        "description": "Rebuild the local SQLite/FTS index from the configured read-only B&R code repository.",
        "inputSchema": object_schema(
            {
                "source_root": {
                    "type": "string",
                    "description": "Optional source root override. The source repository is never modified.",
                    "minLength": 1,
                }
            }
        ),
    },
    {
        "name": "br_get_index_status",
        "description": "Return index paths, timestamp, project/file counts and source classifications.",
        "inputSchema": object_schema({}),
    },
    {
        "name": "br_search_code",
        "description": "Search B&R source with exact and SQLite FTS matching. Prefer origin=user for implementation style.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "origin": {"type": "string", "enum": ["all", "user", "library", "physical"], "default": "all"},
                "language": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
            },
            ["query"],
        ),
    },
    {
        "name": "br_find_symbol",
        "description": "Find an indexed program, action, function block, function, variable or type by name/prefix.",
        "inputSchema": object_schema(
            {
                "name": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "symbol_type": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            ["name"],
        ),
    },
    {
        "name": "br_get_symbol",
        "description": "Return the complete indexed source unit and provenance for one document id.",
        "inputSchema": object_schema(
            {
                "document_id": {"type": "integer", "minimum": 1},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": 100000, "default": 30000},
            },
            ["document_id"],
        ),
    },
    {
        "name": "br_get_program_context",
        "description": "Return a source unit plus bounded sibling Init/Cyclic/Exit/action/VAR/TYP context from its module directory.",
        "inputSchema": object_schema(
            {
                "document_id": {"type": "integer", "minimum": 1},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "default": 30000},
            },
            ["document_id"],
        ),
    },
    {
        "name": "br_get_project_overview",
        "description": "Return Automation Studio version metadata and indexed symbol/language structure for one project.",
        "inputSchema": object_schema(
            {"project": {"type": "string", "minLength": 1}}, ["project"]
        ),
    },
]


class McpServer:
    def __init__(self, index: CodeSearchIndex, source_root: str | None):
        self.index = index
        self.source_root = source_root

    @staticmethod
    def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "structuredContent": payload,
            "isError": is_error,
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        def index_codebase() -> dict[str, Any]:
            source = arguments.get("source_root") or self.source_root
            if not source:
                raise ValueError("No source root configured. Pass --source or source_root.")
            return self.index.rebuild(source)

        calls: dict[str, Callable[[], dict[str, Any]]] = {
            "br_index_codebase": index_codebase,
            "br_get_index_status": self.index.status,
            "br_search_code": lambda: self.index.search(
                arguments["query"],
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
            ),
            "br_find_symbol": lambda: self.index.find_symbol(
                arguments["name"],
                project=arguments.get("project"),
                symbol_type=arguments.get("symbol_type"),
                limit=arguments.get("limit", 20),
            ),
            "br_get_symbol": lambda: self.index.get_symbol(
                arguments["document_id"], max_chars=arguments.get("max_chars", 30000)
            ),
            "br_get_program_context": lambda: self.index.get_context(
                arguments["document_id"], max_chars=arguments.get("max_chars", 30000)
            ),
            "br_get_project_overview": lambda: self.index.project_overview(arguments["project"]),
        }
        call = calls.get(name)
        if call is None:
            return self._text_result({"ok": False, "error": f"Unknown tool: {name}"}, is_error=True)
        try:
            return self._text_result(call())
        except (KeyError, OSError, ValueError, TypeError) as exc:
            return self._text_result({"ok": False, "tool": name, "error": str(exc)}, is_error=True)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        if request_id is None:
            return None
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "br-code-search", "version": __version__},
            }
        elif method == "tools/list":
            result = {"tools": TOOL_DEFINITIONS}
        elif method == "tools/call":
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                result = self._text_result(
                    {"ok": False, "error": "Tool arguments must be an object."}, is_error=True
                )
            else:
                result = self.call_tool(str(params.get("name", "")), arguments)
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                response = self.handle(message)
            except json.JSONDecodeError as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B&R code search stdio MCP server")
    parser.add_argument("--source", default=os.environ.get("BR_CODE_SEARCH_SOURCE"))
    parser.add_argument("--db", default=os.environ.get("BR_CODE_SEARCH_DB", str(DEFAULT_DB)))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    McpServer(CodeSearchIndex(args.db), args.source).run()


if __name__ == "__main__":
    main()
