"""Safe adapters for importing B&R toolchain reports into the reference index.

The code-search process deliberately does not launch Automation Studio, PVITransfer,
or PowerShell.  The registered ``br-plc-toolchain`` MCP remains the execution
boundary; this module only inspects its repository layout and normalizes reports
that were produced by that toolchain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from .core import CodeSearchIndex


DEFAULT_TOOLCHAIN_ROOT = Path(__file__).resolve().parents[2].parent / "br_device_autodev"
REQUIRED_DOCS = (
    "docs/PLC_AUTOMATION_TOOLCHAIN_CONTEXT.md",
    "docs/PLC_TOOLCHAIN_IMPLEMENTATION_PLAN.md",
)
TARGET_CONFIG_CANDIDATES = (
    "tools/plc_targets.local.json",
    "config/local/plc_targets.br_local.json",
    "config/targets/default-safe.json",
)


def _resolve_root(root: str | os.PathLike[str] | None = None) -> Path:
    value = root or os.environ.get("BR_PLC_TOOLCHAIN_ROOT")
    return Path(value).expanduser().resolve() if value else DEFAULT_TOOLCHAIN_ROOT


def inspect_toolchain(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Inspect the expected toolchain repository without executing anything."""
    resolved = _resolve_root(root)
    checks: list[dict[str, Any]] = []
    for relative in REQUIRED_DOCS:
        path = resolved / relative
        checks.append({"name": relative, "ok": path.is_file(), "path": str(path)})
    config_path = next((resolved / item for item in TARGET_CONFIG_CANDIDATES if (resolved / item).is_file()), None)
    checks.append(
        {
            "name": "target_config",
            "ok": config_path is not None,
            "path": str(config_path) if config_path else None,
            "candidates": [str(resolved / item) for item in TARGET_CONFIG_CANDIDATES],
        }
    )
    checks.append({"name": "mcp_server", "ok": (resolved / "mcp_server.py").is_file() or (resolved / "src").is_dir(), "path": str(resolved)})
    checks.append({"name": "reports_directory", "ok": (resolved / "var" / "reports").is_dir(), "path": str(resolved / "var" / "reports")})
    ok = all(item["ok"] for item in checks[:3])
    return {
        "ok": ok,
        "tool": "br_code_search_toolchain_adapter",
        "root": str(resolved),
        "configured": bool(config_path),
        "execution_boundary": "registered br-plc-toolchain MCP",
        "read_only": True,
        "checks": checks,
        "available_operations": ["inspect", "import_report"],
        "blocked_operations": ["build", "download", "write_pvi", "write_opcua"],
        "warnings": [
            "This adapter never invokes Automation Studio or PLC runtime commands.",
            "Use the registered br-plc-toolchain MCP to produce a JSON report first.",
        ],
    }


def _decode_nested(value: Any) -> Any:
    """Unwrap common MCP CallToolResult/JSON-RPC report envelopes."""
    if isinstance(value, dict):
        structured = value.get("structuredContent")
        if isinstance(structured, dict):
            return _decode_nested(structured)
        content = value.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        return _decode_nested(json.loads(item["text"]))
                    except json.JSONDecodeError:
                        continue
        result = value.get("result")
        if isinstance(result, dict):
            return _decode_nested(result)
    return value


def load_report(path: str | os.PathLike[str]) -> tuple[dict[str, Any], str]:
    report_path = Path(path).expanduser().resolve()
    if not report_path.is_file():
        raise ValueError(f"Toolchain report does not exist: {report_path}")
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read JSON toolchain report {report_path}: {exc}") from exc
    raw = _decode_nested(raw)
    if not isinstance(raw, dict):
        raise ValueError("Toolchain report root must be a JSON object")
    return raw, str(report_path)


def _collect_strings(value: Any, keys: set[str], *, limit: int = 50) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in keys:
                if isinstance(item, (list, tuple)):
                    found.extend(str(entry) for entry in item if str(entry).strip())
                elif item is not None and str(item).strip():
                    found.append(str(item))
            elif isinstance(item, (dict, list)):
                found.extend(_collect_strings(item, keys, limit=limit))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_strings(item, keys, limit=limit))
    unique: list[str] = []
    for item in found:
        item = item.strip()
        if item and item not in unique:
            unique.append(item[:2000])
        if len(unique) >= limit:
            break
    return unique


def _first_string(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in keys and item not in (None, ""):
                if isinstance(item, (str, int, float)):
                    return str(item)
            nested = _first_string(item, keys)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _first_string(item, keys)
            if nested:
                return nested
    return None


def normalize_report(
    report: dict[str, Any],
    *,
    report_path: str | None = None,
    source: str = "br-plc-toolchain",
    project: str | None = None,
) -> dict[str, Any]:
    """Convert a build/diagnostic MCP response to a validation-record payload."""
    report = _decode_nested(report)
    if not isinstance(report, dict):
        raise ValueError("Toolchain report must be a JSON object")
    errors = _collect_strings(report, {"errors", "error", "error_summary", "failed_errors"})
    warnings = _collect_strings(report, {"warnings", "warning", "warning_summary"})
    explicit_status = _first_string(report, {"status", "result_status"})
    ok_value = report.get("ok")
    if ok_value is None and isinstance(report.get("data"), dict):
        ok_value = report["data"].get("ok")
    status = str(explicit_status or "").casefold()
    status_aliases = {
        "succeeded": "passed",
        "success": "passed",
        "passed": "passed",
        "pass": "passed",
        "ok": "passed",
        "failed": "failed",
        "failure": "failed",
        "error": "failed",
        "unknown": "unknown",
    }
    status = status_aliases.get(status, "")
    if not status:
        status = "passed" if ok_value is True and not errors else "failed" if ok_value is False or errors else "unknown"
    artifact = _first_string(report, {"artifact", "artifact_path", "package_path", "ruc_package", "output_path", "report_path"})
    as_version = _first_string(report, {"as_version", "automation_studio_version", "studio_version"})
    ar_version = _first_string(report, {"ar_version", "automation_runtime_version", "ssw_version", "arversion"})
    cpu_model = _first_string(report, {"cpu_model", "cputype", "cpu_type", "order_number", "ordernumber"})
    target = _first_string(report, {"target", "target_name"})
    tool = _first_string(report, {"tool", "command"}) or "br-plc-toolchain"
    config = _first_string(report, {"config", "configuration", "configuration_name"})
    report_schema_version = _first_string(report, {"schema_version", "report_schema_version"})
    report_id = _first_string(report, {"event_id", "operation_id", "report_id"})
    log_paths = _collect_strings(report, {"logs", "log_paths", "log_path"}, limit=10)
    next_actions = _collect_strings(report, {"next_actions", "next_action"}, limit=10)
    summary = _first_string(report, {"summary", "message", "next_action"})
    notes_parts = [
        part
        for part in (
            summary,
            f"target={target}" if target else None,
            f"config={config}" if config else None,
            f"tool={tool}",
            f"event={report_id}" if report_id else None,
        )
        if part
    ]
    if report_path:
        notes_parts.append(f"report={report_path}")
    return {
        "project": project or _first_string(report, {"project", "project_name"}),
        "kind": "build",
        "status": status,
        "source": source,
        "as_version": as_version,
        "ar_version": ar_version,
        "cpu_model": cpu_model,
        "artifact": artifact or report_path,
        "notes": "; ".join(notes_parts)[:2000],
        "errors": errors,
        "warnings": warnings,
        "report_path": report_path,
        "tool": tool,
        "target": target,
        "config": config,
        "report_schema_version": report_schema_version,
        "report_id": report_id,
        "log_paths": log_paths,
        "next_actions": next_actions,
    }


def import_report(
    index: "CodeSearchIndex",
    report_path: str | os.PathLike[str],
    *,
    project: str | None = None,
    source: str = "br-plc-toolchain",
    as_version: str | None = None,
    ar_version: str | None = None,
    cpu_model: str | None = None,
) -> dict[str, Any]:
    report, resolved_path = load_report(report_path)
    normalized = normalize_report(report, report_path=resolved_path, source=source, project=project)
    project_name = project or normalized.get("project")
    if not project_name:
        raise ValueError("project is required when the toolchain report does not contain a project name")
    for key, override in (("as_version", as_version), ("ar_version", ar_version), ("cpu_model", cpu_model)):
        if override:
            normalized[key] = override
    recorded = index.record_project_validation(
        str(project_name),
        kind="build",
        status=normalized["status"],
        source=normalized["source"],
        as_version=normalized.get("as_version"),
        ar_version=normalized.get("ar_version"),
        cpu_model=normalized.get("cpu_model"),
        artifact=normalized.get("artifact"),
        notes=normalized.get("notes", ""),
        errors=normalized.get("errors", []),
        warnings=normalized.get("warnings", []),
        tool=normalized.get("tool"),
        target=normalized.get("target"),
        config=normalized.get("config"),
        report_path=normalized.get("report_path"),
        report_schema_version=normalized.get("report_schema_version"),
        report_id=normalized.get("report_id"),
        log_paths=normalized.get("log_paths", []),
        next_actions=normalized.get("next_actions", []),
    )
    return {
        "ok": True,
        "report_path": resolved_path,
        "normalized": normalized,
        **recorded,
    }
