from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .core import CodeSearchIndex
from .evaluation import evaluate_dataset


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
        "description": "Synchronize or rebuild the local SQLite/FTS index from the configured read-only B&R code repository.",
        "inputSchema": object_schema(
            {
                "source_root": {
                    "type": "string",
                    "description": "Optional source root override. The source repository is never modified.",
                    "minLength": 1,
                },
                "mode": {
                    "type": "string",
                    "enum": ["sync", "rebuild"],
                    "default": "sync",
                    "description": "sync updates only changed files; rebuild recreates all indexed documents.",
                },
            }
        ),
    },
    {
        "name": "br_find_similar_code",
        "description": "Find lexical/structural neighbors by a natural-language/code query or an indexed document id.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "reference_document_id": {"type": "integer", "minimum": 1},
                "project": {"type": "string", "minLength": 1},
                "origin": {"type": "string", "enum": ["all", "user", "library", "physical"], "default": "all"},
                "language": {"type": "string", "minLength": 1},
                "symbol_type": {"type": "string", "minLength": 1},
                "as_version": {"type": "string", "minLength": 1, "description": "Automation Studio version prefix."},
                "ar_version": {"type": "string", "minLength": 1, "description": "Automation Runtime version substring; target-aware when available."},
                "cpu_model": {"type": "string", "minLength": 1, "description": "CPU/module model substring; target-aware when available."},
                "library": {"type": "string", "minLength": 1, "description": "Technology package name, for example mapp."},
                "library_version": {"type": "string", "minLength": 1, "description": "Technology package version substring."},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
            }
        ),
    },
    {
        "name": "br_search_hybrid",
        "description": "Combine optional local embedding similarity with exact lexical and B&R structural ranking. Default hashing backend is offline and deterministic; use sentence_transformers with a local model for trained embeddings.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "origin": {"type": "string", "enum": ["all", "user", "library", "physical"], "default": "all"},
                "language": {"type": "string", "minLength": 1},
                "as_version": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
                "library": {"type": "string", "minLength": 1},
                "library_version": {"type": "string", "minLength": 1},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
                "aggregate_files": {"type": "boolean", "default": False},
                "backend": {"type": "string", "enum": ["hashing", "sentence_transformers", "auto"], "default": "hashing"},
                "model": {"type": "string", "minLength": 1},
                "dimension": {"type": "integer", "minimum": 32, "maximum": 4096, "default": 256},
                "semantic_weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "lexical_weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35},
                "structural_weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.15},
                "max_documents": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 50000},
            },
            ["query"],
        ),
    },
    {
        "name": "br_get_index_status",
        "description": "Return index paths, timestamp, project/file counts, target-association coverage and embedding-cache coverage.",
        "inputSchema": object_schema({}),
    },
    {
        "name": "br_get_library_usage",
        "description": "Find projects and source units that declare or use a B&R technology library.",
        "inputSchema": object_schema(
            {
                "library": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100},
            },
            ["library"],
        ),
    },
    {
        "name": "br_find_similar_function_block",
        "description": "Compare only indexed FUNCTION_BLOCK units using the B&R lexical/structural similarity scorer.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "reference_document_id": {"type": "integer", "minimum": 1},
                "project": {"type": "string", "minLength": 1},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
            }
        ),
    },
    {
        "name": "br_get_project_architecture",
        "description": "Return task, module, symbol, language, target, library and validation architecture for one project.",
        "inputSchema": object_schema({"project": {"type": "string", "minLength": 1}}, ["project"]),
    },
    {
        "name": "br_get_embedding_status",
        "description": "Check local embedding backend availability without downloading or loading a model.",
        "inputSchema": object_schema(
            {
                "backend": {"type": "string", "enum": ["hashing", "sentence_transformers", "auto"], "default": "hashing"},
                "model": {"type": "string", "minLength": 1},
                "dimension": {"type": "integer", "minimum": 32, "maximum": 4096, "default": 256},
            }
        ),
    },
    {
        "name": "br_get_qdrant_status",
        "description": "Check optional qdrant-client availability without opening a Qdrant connection.",
        "inputSchema": object_schema({}),
    },
    {
        "name": "br_export_qdrant",
        "description": "Export SQLite-cached vectors and B&R metadata to an explicitly requested local or remote Qdrant collection.",
        "inputSchema": object_schema(
            {
                "path": {"type": "string", "minLength": 1, "description": "Local Qdrant path; defaults to the index var/qdrant directory."},
                "url": {"type": "string", "minLength": 1, "description": "Remote Qdrant URL; mutually exclusive with path."},
                "collection": {"type": "string", "minLength": 1, "default": "br_code_search"},
                "backend": {"type": "string", "enum": ["hashing", "sentence_transformers", "auto"], "default": "hashing"},
                "model": {"type": "string", "minLength": 1},
                "dimension": {"type": "integer", "minimum": 32, "maximum": 4096, "default": 256},
                "project": {"type": "string", "minLength": 1},
                "origin": {"type": "string", "enum": ["all", "user", "library", "physical"], "default": "all"},
                "language": {"type": "string", "minLength": 1},
                "max_documents": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 50000},
                "batch_size": {"type": "integer", "minimum": 1, "maximum": 2048, "default": 256},
                "recreate": {"type": "boolean", "default": False},
            }
        ),
    },
    {
        "name": "br_search_qdrant",
        "description": "Query an explicitly configured Qdrant collection for semantic neighbors and hydrate authoritative source text from SQLite.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1, "description": "Local Qdrant path; mutually exclusive with url."},
                "url": {"type": "string", "minLength": 1, "description": "Remote Qdrant URL; mutually exclusive with path."},
                "collection": {"type": "string", "minLength": 1, "default": "br_code_search"},
                "backend": {"type": "string", "enum": ["hashing", "sentence_transformers", "auto"], "default": "hashing"},
                "model": {"type": "string", "minLength": 1},
                "dimension": {"type": "integer", "minimum": 32, "maximum": 4096, "default": 256},
                "project": {"type": "string", "minLength": 1},
                "origin": {"type": "string", "enum": ["all", "user", "library", "physical"], "default": "all"},
                "language": {"type": "string", "minLength": 1},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
                "score_threshold": {"type": "number", "minimum": -1, "maximum": 1},
            },
            ["query"],
        ),
    },
    {
        "name": "br_get_toolchain_status",
        "description": "Inspect the sibling B&R toolchain repository and report safe read-only adapter capabilities; never executes PLC commands.",
        "inputSchema": object_schema(
            {
                "root": {"type": "string", "minLength": 1, "description": "Optional br_device_autodev root override."},
            }
        ),
    },
    {
        "name": "br_import_toolchain_report",
        "description": "Import a JSON report produced by the registered br-plc-toolchain MCP into project build history without launching Automation Studio or changing a PLC.",
        "inputSchema": object_schema(
            {
                "report_path": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1, "description": "Indexed project name; required when the report has no project field."},
                "source": {"type": "string", "default": "br-plc-toolchain"},
                "as_version": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
            },
            ["report_path"],
        ),
    },
    {
        "name": "br_record_project_validation",
        "description": "Persist external build, field verification or version-compatibility feedback outside the source repository.",
        "inputSchema": object_schema(
            {
                "project": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "enum": ["build", "field", "compatibility"]},
                "status": {"type": "string", "enum": ["passed", "failed", "unknown"]},
                "source": {"type": "string", "default": "external"},
                "as_version": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
                "artifact": {"type": "string", "minLength": 1},
                "notes": {"type": "string", "default": ""},
                "errors": {"type": "array", "items": {"type": "string"}, "default": []},
                "warnings": {"type": "array", "items": {"type": "string"}, "default": []},
                "tool": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
                "config": {"type": "string", "minLength": 1},
                "report_path": {"type": "string", "minLength": 1},
                "report_schema_version": {"type": "string", "minLength": 1},
                "report_id": {"type": "string", "minLength": 1},
                "log_paths": {"type": "array", "items": {"type": "string"}, "default": []},
                "next_actions": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            ["project", "kind", "status"],
        ),
    },
    {
        "name": "br_get_compile_history",
        "description": "Return recorded external build results and diagnostics for one project.",
        "inputSchema": object_schema(
            {
                "project": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["passed", "failed", "unknown"]},
                "as_version": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
                "tool": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
            ["project"],
        ),
    },
    {
        "name": "br_search_build_errors",
        "description": "Search recorded build errors, warnings and diagnostic notes across indexed projects.",
        "inputSchema": object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            ["query"],
        ),
    },
    {
        "name": "br_get_build_diagnostic_summary",
        "description": "Aggregate imported B&R build statuses, repeated errors, warnings and recent report metadata across projects.",
        "inputSchema": object_schema(
            {
                "project": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["passed", "failed", "unknown"]},
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            }
        ),
    },
    {
        "name": "br_evaluate_retrieval",
        "description": "Run a versioned JSON retrieval dataset and report Hit@K/MRR without modifying the source repository.",
        "inputSchema": object_schema(
            {
                "dataset_path": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                "max_cases": {"type": "integer", "minimum": 1},
            },
            ["dataset_path"],
        ),
    },
    {
        "name": "br_annotate_project",
        "description": "Persist a quality/verification annotation in the tool metadata directory without modifying the source project.",
        "inputSchema": object_schema(
            {
                "project": {"type": "string", "minLength": 1},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"], "default": "normal"},
                "verified": {"type": "boolean", "default": False},
                "deprecated": {"type": "boolean", "default": False},
                "do_not_copy": {"type": "boolean", "default": False},
                "notes": {"type": "string", "default": ""},
            },
            ["project"],
        ),
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
                "as_version": {"type": "string", "minLength": 1, "description": "Automation Studio version prefix."},
                "ar_version": {"type": "string", "minLength": 1, "description": "Automation Runtime version substring; target-aware when available."},
                "cpu_model": {"type": "string", "minLength": 1, "description": "CPU/module model substring; target-aware when available."},
                "library": {"type": "string", "minLength": 1, "description": "Technology package name, for example mapp."},
                "library_version": {"type": "string", "minLength": 1, "description": "Technology package version substring."},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "include_source": {"type": "boolean", "default": True},
                "max_chars_per_result": {"type": "integer", "minimum": 200, "maximum": 30000, "default": 4000},
                "aggregate_files": {"type": "boolean", "default": False, "description": "Group matching units by project-relative file."},
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
                "as_version": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
                "library": {"type": "string", "minLength": 1},
                "library_version": {"type": "string", "minLength": 1},
                "quality": {"type": "string", "enum": ["gold", "normal", "deprecated"]},
                "verified_only": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
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
        "description": "Return a source unit plus bounded sibling Init/Cyclic/Exit/action/VAR/TYP context, parsed variable declarations and resolved type references.",
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
    {
        "name": "br_get_task_configuration",
        "description": "Return B&R .sw TaskClass/Task assignments, source programs and explicit cycle metadata for one project.",
        "inputSchema": object_schema(
            {
                "project": {"type": "string", "minLength": 1},
                "task_name": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "cpu_model": {"type": "string", "minLength": 1},
                "ar_version": {"type": "string", "minLength": 1},
            },
            ["project"],
        ),
    },
    {
        "name": "br_get_type_definition",
        "description": "Return indexed B&R TYPE declarations with their source and project provenance.",
        "inputSchema": object_schema(
            {
                "type_name": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
            },
            ["type_name"],
        ),
    },
    {
        "name": "br_find_references",
        "description": "Find whole-identifier references to a B&R symbol or variable with declaration/use and read/write/call/member access classification.",
        "inputSchema": object_schema(
            {
                "name": {"type": "string", "minLength": 1},
                "project": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            ["name"],
        ),
    },
    {
        "name": "br_compare_implementations",
        "description": "Compare two indexed source units with provenance, validation metadata and a bounded unified diff.",
        "inputSchema": object_schema(
            {
                "left_document_id": {"type": "integer", "minimum": 1},
                "right_document_id": {"type": "integer", "minimum": 1},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "default": 30000},
            },
            ["left_document_id", "right_document_id"],
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
            mode = arguments.get("mode", "sync")
            return self.index.rebuild(source) if mode == "rebuild" else self.index.sync(source)

        calls: dict[str, Callable[[], dict[str, Any]]] = {
            "br_index_codebase": index_codebase,
            "br_get_index_status": self.index.status,
            "br_get_library_usage": lambda: self.index.get_library_usage(
                arguments["library"], project=arguments.get("project"), limit=arguments.get("limit", 100)
            ),
            "br_find_similar_function_block": lambda: self.index.find_similar_function_block(
                arguments.get("query"),
                reference_document_id=arguments.get("reference_document_id"),
                project=arguments.get("project"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
            ),
            "br_get_project_architecture": lambda: self.index.get_project_architecture(arguments["project"]),
            "br_get_embedding_status": lambda: self.index.embedding_status(
                arguments.get("backend", "hashing"),
                model=arguments.get("model"),
                dimension=arguments.get("dimension", 256),
            ),
            "br_get_qdrant_status": self.index.qdrant_status,
            "br_export_qdrant": lambda: self.index.export_qdrant(
                path=arguments.get("path"),
                url=arguments.get("url"),
                collection=arguments.get("collection", "br_code_search"),
                backend=arguments.get("backend", "hashing"),
                model=arguments.get("model"),
                dimension=arguments.get("dimension", 256),
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                max_documents=arguments.get("max_documents", 50000),
                batch_size=arguments.get("batch_size", 256),
                recreate=arguments.get("recreate", False),
            ),
            "br_search_qdrant": lambda: self.index.search_qdrant(
                arguments["query"],
                path=arguments.get("path"),
                url=arguments.get("url"),
                collection=arguments.get("collection", "br_code_search"),
                backend=arguments.get("backend", "hashing"),
                model=arguments.get("model"),
                dimension=arguments.get("dimension", 256),
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
                score_threshold=arguments.get("score_threshold"),
            ),
            "br_get_toolchain_status": lambda: self.index.toolchain_status(arguments.get("root")),
            "br_import_toolchain_report": lambda: self.index.import_toolchain_report(
                arguments["report_path"],
                project=arguments.get("project"),
                source=arguments.get("source", "br-plc-toolchain"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
            ),
            "br_record_project_validation": lambda: self.index.record_project_validation(
                arguments["project"],
                kind=arguments["kind"],
                status=arguments["status"],
                source=arguments.get("source", "external"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                artifact=arguments.get("artifact"),
                notes=arguments.get("notes", ""),
                errors=arguments.get("errors", []),
                warnings=arguments.get("warnings", []),
                tool=arguments.get("tool"),
                target=arguments.get("target"),
                config=arguments.get("config"),
                report_path=arguments.get("report_path"),
                report_schema_version=arguments.get("report_schema_version"),
                report_id=arguments.get("report_id"),
                log_paths=arguments.get("log_paths", []),
                next_actions=arguments.get("next_actions", []),
            ),
            "br_get_compile_history": lambda: self.index.get_compile_history(
                arguments["project"],
                status=arguments.get("status"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                target=arguments.get("target"),
                tool=arguments.get("tool"),
                limit=arguments.get("limit", 50),
            ),
            "br_search_build_errors": lambda: self.index.search_build_errors(
                arguments["query"], project=arguments.get("project"), limit=arguments.get("limit", 100)
            ),
            "br_get_build_diagnostic_summary": lambda: self.index.get_build_diagnostic_summary(
                project=arguments.get("project"),
                status=arguments.get("status"),
                query=arguments.get("query"),
                limit=arguments.get("limit", 20),
            ),
            "br_evaluate_retrieval": lambda: evaluate_dataset(
                self.index,
                arguments["dataset_path"],
                top_k=arguments.get("top_k", 5),
                max_cases=arguments.get("max_cases"),
            ),
            "br_annotate_project": lambda: self.index.annotate_project(
                arguments["project"],
                quality=arguments.get("quality", "normal"),
                verified=arguments.get("verified", False),
                deprecated=arguments.get("deprecated", False),
                do_not_copy=arguments.get("do_not_copy", False),
                notes=arguments.get("notes", ""),
            ),
            "br_search_code": lambda: self.index.search(
                arguments["query"],
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                library=arguments.get("library"),
                library_version=arguments.get("library_version"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
                aggregate_files=arguments.get("aggregate_files", False),
            ),
            "br_find_similar_code": lambda: self.index.search_similar(
                arguments.get("query"),
                reference_document_id=arguments.get("reference_document_id"),
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                symbol_type=arguments.get("symbol_type"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                library=arguments.get("library"),
                library_version=arguments.get("library_version"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
            ),
            "br_search_hybrid": lambda: self.index.search_hybrid(
                arguments["query"],
                project=arguments.get("project"),
                origin=arguments.get("origin"),
                language=arguments.get("language"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                library=arguments.get("library"),
                library_version=arguments.get("library_version"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 10),
                include_source=arguments.get("include_source", True),
                max_chars_per_result=arguments.get("max_chars_per_result", 4000),
                aggregate_files=arguments.get("aggregate_files", False),
                backend=arguments.get("backend", "hashing"),
                model=arguments.get("model"),
                dimension=arguments.get("dimension", 256),
                semantic_weight=arguments.get("semantic_weight", 0.5),
                lexical_weight=arguments.get("lexical_weight", 0.35),
                structural_weight=arguments.get("structural_weight", 0.15),
                max_documents=arguments.get("max_documents", 50000),
            ),
            "br_find_symbol": lambda: self.index.find_symbol(
                arguments["name"],
                project=arguments.get("project"),
                symbol_type=arguments.get("symbol_type"),
                as_version=arguments.get("as_version"),
                ar_version=arguments.get("ar_version"),
                cpu_model=arguments.get("cpu_model"),
                library=arguments.get("library"),
                library_version=arguments.get("library_version"),
                quality=arguments.get("quality"),
                verified_only=arguments.get("verified_only", False),
                include_deprecated=arguments.get("include_deprecated", False),
                limit=arguments.get("limit", 20),
            ),
            "br_get_symbol": lambda: self.index.get_symbol(
                arguments["document_id"], max_chars=arguments.get("max_chars", 30000)
            ),
            "br_get_program_context": lambda: self.index.get_context(
                arguments["document_id"], max_chars=arguments.get("max_chars", 30000)
            ),
            "br_get_project_overview": lambda: self.index.project_overview(arguments["project"]),
            "br_get_task_configuration": lambda: self.index.get_task_configuration(
                arguments["project"],
                task_name=arguments.get("task_name"),
                source=arguments.get("source"),
                cpu_model=arguments.get("cpu_model"),
                ar_version=arguments.get("ar_version"),
            ),
            "br_get_type_definition": lambda: self.index.get_type_definition(
                arguments["type_name"], project=arguments.get("project")
            ),
            "br_find_references": lambda: self.index.find_references(
                arguments["name"], project=arguments.get("project"), limit=arguments.get("limit", 100)
            ),
            "br_compare_implementations": lambda: self.index.compare_implementations(
                arguments["left_document_id"],
                arguments["right_document_id"],
                max_chars=arguments.get("max_chars", 30000),
            ),
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
        # MCP transports JSON as UTF-8 even when a Windows console defaults to a
        # legacy code page; reconfigure so Chinese/ANSI B&R source cannot break
        # the protocol with UnicodeEncodeError.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except AttributeError:
            pass
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
