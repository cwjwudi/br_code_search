from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_EXTENSIONS = {".st", ".fun", ".var", ".typ", ".c", ".h", ".apj", ".pkg"}
IGNORED_DIRECTORIES = {
    ".git",
    ".svn",
    "temp",
    "binaries",
    "binary",
    "diagnostics",
    "upgrade",
    "asam",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u3400-\u9fff]+")
ST_HEADER_RE = re.compile(
    r"^\s*(?:\{[^}\r\n]*\}\s*)*(PROGRAM|FUNCTION_BLOCK|FUNCTION|ACTION)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
VAR_DECL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?);(?:\s*\(\*.*)?$")
TYPE_HEADER_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(STRUCT\b|\(|ARRAY\b|[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
C_FUNCTION_RE = re.compile(
    r"^\s*(?!if\b|for\b|while\b|switch\b)(?:static\s+|inline\s+|extern\s+)*"
    r"[A-Za-z_][\w\s\*]*?\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{",
    re.IGNORECASE,
)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL,
    project_file TEXT,
    as_version TEXT,
    project_version TEXT,
    description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    language TEXT NOT NULL,
    origin TEXT NOT NULL,
    symbol_name TEXT,
    symbol_type TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_symbol ON documents(symbol_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_documents_project_path ON documents(project_id, relative_path);
CREATE INDEX IF NOT EXISTS idx_documents_origin ON documents(origin);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    symbol_name,
    relative_path,
    project_name,
    content,
    tokenize='unicode61 remove_diacritics 2'
);
"""


@dataclass(slots=True)
class ParsedUnit:
    symbol_name: str | None
    symbol_type: str
    start_line: int
    end_line: int
    content: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_source(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        gb_text = raw.decode("gb18030")
    except UnicodeDecodeError:
        gb_text = None
    cp_text = raw.decode("cp1252", errors="replace")
    # Western ANSI source containing umlauts can also be decoded as GB18030,
    # but commonly produces one accidental CJK character. Require a small run
    # of CJK text before preferring the Chinese code page.
    if gb_text is not None:
        cjk_count = sum("\u3400" <= char <= "\u9fff" for char in gb_text)
        if cjk_count >= 2:
            return gb_text, "gb18030"
    return cp_text, "cp1252"


def language_for(path: Path) -> str:
    return {
        ".st": "structured_text",
        ".fun": "structured_text_interface",
        ".var": "iec_variables",
        ".typ": "iec_types",
        ".c": "c",
        ".h": "c_header",
        ".apj": "automation_studio_project",
        ".pkg": "automation_studio_package",
    }[path.suffix.lower()]


def origin_for(relative_path: str) -> str:
    normalized = "/" + relative_path.replace("\\", "/").casefold() + "/"
    if "/logical/libraries/" in normalized:
        return "library"
    if "/physical/" in normalized:
        return "physical"
    return "user"


def _unit(lines: list[str], start: int, end: int, name: str | None, kind: str) -> ParsedUnit:
    return ParsedUnit(name, kind.lower(), start + 1, end, "\n".join(lines[start:end]))


def parse_st_units(text: str) -> list[ParsedUnit]:
    lines = text.splitlines()
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = ST_HEADER_RE.match(line)
        if match:
            starts.append((index, match.group(1).upper(), match.group(2)))
    units: list[ParsedUnit] = []
    end_names = {
        "PROGRAM": "END_PROGRAM",
        "FUNCTION_BLOCK": "END_FUNCTION_BLOCK",
        "FUNCTION": "END_FUNCTION",
        "ACTION": "END_ACTION",
    }
    for position, (start, kind, name) in enumerate(starts):
        next_start = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        end = next_start
        terminator = end_names[kind]
        for line_index in range(start + 1, next_start):
            if re.match(rf"^\s*{terminator}\b", lines[line_index], re.IGNORECASE):
                end = line_index + 1
                break
        units.append(_unit(lines, start, end, name, kind))
    return units


def parse_var_units(text: str, stem: str) -> list[ParsedUnit]:
    lines = text.splitlines()
    units: list[ParsedUnit] = []
    block_index = 0
    index = 0
    while index < len(lines):
        if not re.match(r"^\s*VAR(?:_|\s|$)", lines[index], re.IGNORECASE):
            index += 1
            continue
        start = index
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            if re.match(r"^\s*END_VAR\b", lines[cursor], re.IGNORECASE):
                end = cursor + 1
                break
        block_index += 1
        kind_match = re.match(r"^\s*(VAR(?:_[A-Z_]+)?(?:\s+\w+)?)", lines[start], re.IGNORECASE)
        kind = (kind_match.group(1) if kind_match else "VAR").upper().replace(" ", "_")
        units.append(_unit(lines, start, end, f"{stem}:{kind}:{block_index}", "variable_block"))
        for cursor in range(start + 1, end):
            declaration = VAR_DECL_RE.match(lines[cursor].split("//", 1)[0])
            if declaration:
                units.append(_unit(lines, cursor, cursor + 1, declaration.group(1), "variable"))
        index = end
    return units


def parse_type_units(text: str) -> list[ParsedUnit]:
    lines = text.splitlines()
    units: list[ParsedUnit] = []
    in_type = False
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if re.match(r"^TYPE\b", stripped, re.IGNORECASE):
            in_type = True
            index += 1
            continue
        if re.match(r"^END_TYPE\b", stripped, re.IGNORECASE):
            in_type = False
            index += 1
            continue
        if not in_type:
            index += 1
            continue
        match = TYPE_HEADER_RE.match(lines[index])
        if not match:
            index += 1
            continue
        name = match.group(1)
        rhs = match.group(2).upper()
        end = index + 1
        if rhs == "STRUCT":
            for cursor in range(index + 1, len(lines)):
                if re.match(r"^\s*END_STRUCT\s*;?", lines[cursor], re.IGNORECASE):
                    end = cursor + 1
                    break
        elif rhs == "(":
            for cursor in range(index + 1, len(lines)):
                if re.search(r"\)\s*;", lines[cursor]):
                    end = cursor + 1
                    break
        units.append(_unit(lines, index, end, name, "data_type"))
        index = end
    return units


def parse_c_units(text: str) -> list[ParsedUnit]:
    lines = text.splitlines()
    units: list[ParsedUnit] = []
    for index, line in enumerate(lines):
        match = C_FUNCTION_RE.match(line)
        if not match:
            continue
        depth = line.count("{") - line.count("}")
        end = index + 1
        while depth > 0 and end < len(lines):
            depth += lines[end].count("{") - lines[end].count("}")
            end += 1
        units.append(_unit(lines, index, end, match.group(1), "c_function"))
    return units


def parse_units(path: Path, text: str) -> list[ParsedUnit]:
    suffix = path.suffix.lower()
    if suffix in {".st", ".fun"}:
        units = parse_st_units(text)
    elif suffix == ".var":
        units = parse_var_units(text, path.stem)
    elif suffix == ".typ":
        units = parse_type_units(text)
    elif suffix in {".c", ".h"}:
        units = parse_c_units(text)
    else:
        units = []
    if units:
        return units
    lines = text.splitlines()
    fallback_type = {
        ".st": "source_file",
        ".fun": "interface_file",
        ".var": "variable_file",
        ".typ": "type_file",
        ".c": "c_file",
        ".h": "header_file",
        ".apj": "project_metadata",
        ".pkg": "package_metadata",
    }[suffix]
    return [ParsedUnit(path.stem, fallback_type, 1, max(1, len(lines)), text)]


def parse_project_file(path: Path) -> dict[str, Any]:
    text, _ = read_source(path)
    as_match = re.search(r"<\?AutomationStudio\s+Version=\"?([^\"?]+)", text)
    project_match = re.search(r"<Project\b([^>]*)>", text)
    attrs = project_match.group(1) if project_match else ""
    def attr(name: str) -> str | None:
        found = re.search(rf"\b{name}=\"([^\"]*)\"", attrs)
        return found.group(1) if found else None
    technologies = {
        name: version
        for name, version in re.findall(r"<([A-Za-z_][\w.-]*)\b[^>]*\bVersion=\"([^\"]+)\"", text)
        if name not in {"Project"}
    }
    return {
        "as_version": as_match.group(1).strip() if as_match else None,
        "project_version": attr("Version"),
        "description": attr("Description"),
        "technology_packages": technologies,
    }


def _should_index(path: Path, source_root: Path) -> bool:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    try:
        relative_parts = path.relative_to(source_root).parts
    except ValueError:
        return False
    if any(part.casefold() in IGNORED_DIRECTORIES for part in relative_parts[:-1]):
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


class CodeSearchIndex:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)

    def rebuild(self, source_root: str | Path) -> dict[str, Any]:
        root = Path(source_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Source root does not exist or is not a directory: {root}")
        project_files = sorted(root.rglob("*.apj"))
        project_roots = {path.parent.resolve(): path for path in project_files}
        if not project_roots:
            project_roots[root] = None
        warnings: list[str] = []
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            connection.execute("DELETE FROM documents_fts")
            connection.execute("DELETE FROM documents")
            connection.execute("DELETE FROM projects")
            project_ids: dict[Path, int] = {}
            for project_root, project_file in project_roots.items():
                metadata = parse_project_file(project_file) if project_file else {}
                relative_project_file = (
                    project_file.relative_to(project_root).as_posix() if project_file else None
                )
                cursor = connection.execute(
                    """INSERT INTO projects
                    (name, root_path, project_file, as_version, project_version, description, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_root.name,
                        str(project_root),
                        relative_project_file,
                        metadata.get("as_version"),
                        metadata.get("project_version"),
                        metadata.get("description"),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                project_ids[project_root] = int(cursor.lastrowid)
            indexed_files = 0
            indexed_documents = 0
            for path in sorted(root.rglob("*")):
                if not path.is_file() or not _should_index(path, root):
                    continue
                candidates = [candidate for candidate in project_roots if path.is_relative_to(candidate)]
                project_root = max(candidates, key=lambda item: len(item.parts)) if candidates else root
                if project_root not in project_ids:
                    continue
                try:
                    text, encoding = read_source(path)
                except OSError as exc:
                    warnings.append(f"Could not read {path}: {exc}")
                    continue
                relative_path = path.relative_to(project_root).as_posix()
                origin = origin_for(relative_path)
                project_name = project_root.name
                units = parse_units(path, text)
                indexed_files += 1
                for unit in units:
                    digest = hashlib.sha256(unit.content.encode("utf-8", errors="replace")).hexdigest()
                    cursor = connection.execute(
                        """INSERT INTO documents
                        (project_id, relative_path, language, origin, symbol_name, symbol_type,
                         start_line, end_line, encoding, content_hash, content)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            project_ids[project_root],
                            relative_path,
                            language_for(path),
                            origin,
                            unit.symbol_name,
                            unit.symbol_type,
                            unit.start_line,
                            unit.end_line,
                            encoding,
                            digest,
                            unit.content,
                        ),
                    )
                    document_id = int(cursor.lastrowid)
                    connection.execute(
                        "INSERT INTO documents_fts(rowid, symbol_name, relative_path, project_name, content) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (document_id, unit.symbol_name or "", relative_path, project_name, unit.content),
                    )
                    indexed_documents += 1
            meta = {
                "schema_version": "1",
                "source_root": str(root),
                "indexed_at": utc_now(),
                "tool_version": "0.1.0",
            }
            connection.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", meta.items()
            )
        return {
            "ok": True,
            "source_root": str(root),
            "database": str(self.database_path),
            "projects": len(project_ids),
            "files": indexed_files,
            "documents": indexed_documents,
            "warnings": warnings[:50],
        }

    def status(self) -> dict[str, Any]:
        if not self.database_path.exists():
            return {"ok": False, "database": str(self.database_path), "indexed": False}
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
            projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            files = connection.execute("SELECT COUNT(DISTINCT project_id || ':' || relative_path) FROM documents").fetchone()[0]
            origins = {
                row["origin"]: row["count"]
                for row in connection.execute(
                    "SELECT origin, COUNT(*) AS count FROM documents GROUP BY origin"
                )
            }
        return {
            "ok": True,
            "indexed": bool(meta.get("indexed_at")),
            "database": str(self.database_path),
            "source_root": meta.get("source_root"),
            "indexed_at": meta.get("indexed_at"),
            "projects": projects,
            "files": files,
            "documents": documents,
            "documents_by_origin": origins,
        }

    @staticmethod
    def _row_payload(row: sqlite3.Row, *, include_source: bool, max_chars: int = 6000) -> dict[str, Any]:
        payload = {
            "document_id": row["id"],
            "project": row["project_name"],
            "path": row["relative_path"],
            "language": row["language"],
            "origin": row["origin"],
            "symbol": row["symbol_name"],
            "symbol_type": row["symbol_type"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "encoding": row["encoding"],
        }
        if include_source:
            source = row["content"]
            payload["source"] = source[:max_chars]
            payload["source_truncated"] = len(source) > max_chars
        return payload

    def _ensure_index(self) -> None:
        if not self.database_path.exists():
            raise ValueError("Index database does not exist. Call br_index_codebase first.")

    def search(
        self,
        query: str,
        *,
        project: str | None = None,
        origin: str | None = None,
        language: str | None = None,
        limit: int = 10,
        include_source: bool = True,
        max_chars_per_result: int = 4000,
    ) -> dict[str, Any]:
        self._ensure_index()
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        limit = max(1, min(int(limit), 50))
        tokens = IDENTIFIER_RE.findall(query)
        fts_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens[:12])
        filters: list[str] = []
        parameters: list[Any] = []
        if project:
            filters.append("p.name = ? COLLATE NOCASE")
            parameters.append(project)
        if origin and origin != "all":
            filters.append("d.origin = ?")
            parameters.append(origin)
        if language:
            filters.append("d.language = ?")
            parameters.append(language)
        filter_sql = (" AND " + " AND ".join(filters)) if filters else ""
        base_columns = """d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
            d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content"""
        rows_by_id: dict[int, sqlite3.Row] = {}
        with closing(self.connect()) as connection, connection:
            exact_rows = connection.execute(
                f"""SELECT {base_columns}
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE (d.symbol_name = ? COLLATE NOCASE OR d.content LIKE ? OR d.relative_path LIKE ?)
                {filter_sql}
                ORDER BY CASE WHEN d.symbol_name = ? COLLATE NOCASE THEN 0
                              WHEN d.symbol_name LIKE ? THEN 1 ELSE 2 END,
                         CASE d.symbol_type
                              WHEN 'program' THEN 0 WHEN 'action' THEN 0
                              WHEN 'function_block' THEN 0 WHEN 'function' THEN 0
                              WHEN 'c_function' THEN 0 WHEN 'source_file' THEN 0
                              WHEN 'variable_block' THEN 1 WHEN 'data_type' THEN 1
                              WHEN 'variable' THEN 2 ELSE 3 END,
                         CASE d.origin WHEN 'user' THEN 0 WHEN 'library' THEN 1 ELSE 2 END,
                         length(d.content)
                LIMIT ?""",
                [query, f"%{query}%", f"%{query}%", *parameters, query, f"{query}%", limit],
            ).fetchall()
            for row in exact_rows:
                rows_by_id[int(row["id"])] = row
            if fts_query and len(rows_by_id) < limit:
                fts_rows = connection.execute(
                    f"""SELECT {base_columns}
                    FROM documents_fts f
                    JOIN documents d ON d.id=f.rowid
                    JOIN projects p ON p.id=d.project_id
                    WHERE documents_fts MATCH ? {filter_sql}
                    ORDER BY CASE d.symbol_type
                                  WHEN 'program' THEN 0 WHEN 'action' THEN 0
                                  WHEN 'function_block' THEN 0 WHEN 'function' THEN 0
                                  WHEN 'c_function' THEN 0 WHEN 'source_file' THEN 0
                                  WHEN 'variable_block' THEN 1 WHEN 'data_type' THEN 1
                                  WHEN 'variable' THEN 2 ELSE 3 END,
                             bm25(documents_fts),
                             CASE d.origin WHEN 'user' THEN 0 WHEN 'library' THEN 1 ELSE 2 END
                    LIMIT ?""",
                    [fts_query, *parameters, limit],
                ).fetchall()
                for row in fts_rows:
                    rows_by_id.setdefault(int(row["id"]), row)
        rows = list(rows_by_id.values())[:limit]
        return {
            "ok": True,
            "query": query,
            "count": len(rows),
            "results": [
                self._row_payload(row, include_source=include_source, max_chars=max_chars_per_result)
                for row in rows
            ],
        }

    def find_symbol(
        self,
        name: str,
        *,
        project: str | None = None,
        symbol_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        self._ensure_index()
        filters = ["d.symbol_name LIKE ?"]
        params: list[Any] = [f"{name.strip()}%"]
        if project:
            filters.append("p.name = ? COLLATE NOCASE")
            params.append(project)
        if symbol_type:
            filters.append("d.symbol_type = ? COLLATE NOCASE")
            params.append(symbol_type)
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                f"""SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE {' AND '.join(filters)}
                ORDER BY CASE WHEN d.symbol_name = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                         CASE d.origin WHEN 'user' THEN 0 WHEN 'library' THEN 1 ELSE 2 END,
                         p.name, d.relative_path LIMIT ?""",
                [*params, name.strip(), max(1, min(int(limit), 100))],
            ).fetchall()
        return {
            "ok": True,
            "name": name,
            "count": len(rows),
            "results": [self._row_payload(row, include_source=False) for row in rows],
        }

    def get_symbol(self, document_id: int, *, max_chars: int = 30000) -> dict[str, Any]:
        self._ensure_index()
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content
                FROM documents d JOIN projects p ON p.id=d.project_id WHERE d.id=?""",
                (int(document_id),),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown document_id: {document_id}")
        return {"ok": True, "result": self._row_payload(row, include_source=True, max_chars=max_chars)}

    def get_context(self, document_id: int, *, max_chars: int = 30000) -> dict[str, Any]:
        self._ensure_index()
        budget = max(1000, min(int(max_chars), 100000))
        with closing(self.connect()) as connection, connection:
            primary = connection.execute(
                """SELECT d.id, d.project_id, p.name AS project_name, d.relative_path, d.language,
                d.origin, d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content
                FROM documents d JOIN projects p ON p.id=d.project_id WHERE d.id=?""",
                (int(document_id),),
            ).fetchone()
            if primary is None:
                raise ValueError(f"Unknown document_id: {document_id}")
            parent = str(Path(primary["relative_path"]).parent).replace("\\", "/")
            prefix = "" if parent == "." else parent + "/"
            related = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE d.project_id=? AND d.id<>? AND d.relative_path LIKE ?
                ORDER BY CASE d.symbol_type
                    WHEN 'variable_block' THEN 0 WHEN 'data_type' THEN 1
                    WHEN 'program' THEN 2 WHEN 'action' THEN 3 ELSE 4 END,
                    d.relative_path, d.start_line""",
                (primary["project_id"], int(document_id), prefix + "%"),
            ).fetchall()
        primary_payload = self._row_payload(primary, include_source=True, max_chars=budget)
        used = len(primary_payload.get("source", ""))
        seen_paths = {primary["relative_path"]}
        context: list[dict[str, Any]] = []
        for row in related:
            if row["relative_path"] in seen_paths:
                continue
            remaining = budget - used
            if remaining < 300:
                break
            payload = self._row_payload(row, include_source=True, max_chars=remaining)
            context.append(payload)
            used += len(payload.get("source", ""))
            seen_paths.add(row["relative_path"])
        return {
            "ok": True,
            "primary": primary_payload,
            "related_context": context,
            "context_chars": used,
            "context_truncated": used >= budget,
            "note": "Related context is directory-based in v0.1 and is not a compiler-grade dependency graph.",
        }

    def project_overview(self, project: str) -> dict[str, Any]:
        self._ensure_index()
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE name=? COLLATE NOCASE", (project,)
            ).fetchone()
            if row is None:
                names = [item[0] for item in connection.execute("SELECT name FROM projects ORDER BY name")]
                raise ValueError(f"Unknown project: {project}. Available projects: {names}")
            type_counts = {
                item["symbol_type"]: item["count"]
                for item in connection.execute(
                    "SELECT symbol_type, COUNT(*) AS count FROM documents WHERE project_id=? GROUP BY symbol_type",
                    (row["id"],),
                )
            }
            language_counts = {
                item["language"]: item["count"]
                for item in connection.execute(
                    "SELECT language, COUNT(*) AS count FROM documents WHERE project_id=? GROUP BY language",
                    (row["id"],),
                )
            }
            top_paths = [
                item["top_path"]
                for item in connection.execute(
                    """SELECT DISTINCT CASE WHEN instr(relative_path, '/') > 0
                    THEN substr(relative_path, 1, instr(relative_path, '/') - 1)
                    ELSE relative_path END AS top_path
                    FROM documents WHERE project_id=? ORDER BY top_path LIMIT 100""",
                    (row["id"],),
                )
            ]
        return {
            "ok": True,
            "project": row["name"],
            "root_path": row["root_path"],
            "project_file": row["project_file"],
            "as_version": row["as_version"],
            "project_version": row["project_version"],
            "description": row["description"],
            "metadata": json.loads(row["metadata_json"]),
            "documents_by_type": type_counts,
            "documents_by_language": language_counts,
            "top_level_paths": top_paths,
        }
