from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_EXTENSIONS = {".st", ".fun", ".var", ".typ", ".c", ".h", ".apj", ".pkg", ".sw"}
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
    metadata_json TEXT NOT NULL DEFAULT '{}',
    quality TEXT NOT NULL DEFAULT 'normal',
    verified INTEGER NOT NULL DEFAULT 0,
    deprecated INTEGER NOT NULL DEFAULT 0,
    do_not_copy INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    software_path TEXT NOT NULL,
    task_class TEXT NOT NULL,
    task_name TEXT NOT NULL,
    source TEXT NOT NULL,
    language TEXT,
    description TEXT NOT NULL DEFAULT '',
    number TEXT,
    cycle_time_us INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_source ON tasks(project_id, source);
CREATE INDEX IF NOT EXISTS idx_tasks_project_name ON tasks(project_id, task_name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS source_files (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY(project_id, relative_path)
);
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
        ".sw": "automation_studio_software",
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


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cycle_value(attributes: dict[str, str]) -> int | None:
    for key in ("CycleTimeUs", "CycleTime", "PeriodUs", "Period", "IntervalUs", "Interval"):
        value = attributes.get(key)
        if not value:
            continue
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return None


def parse_software_tasks(text: str, relative_path: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    tasks: list[dict[str, Any]] = []
    for task_class in root.iter():
        if _xml_local_name(task_class.tag) != "TaskClass":
            continue
        class_name = task_class.attrib.get("Name", "")
        for task in task_class:
            if _xml_local_name(task.tag) != "Task":
                continue
            attrs = dict(task.attrib)
            tasks.append(
                {
                    "software_path": relative_path,
                    "task_class": class_name,
                    "task_name": attrs.get("Name", ""),
                    "source": attrs.get("Source", ""),
                    "language": attrs.get("Language"),
                    "description": attrs.get("Description", ""),
                    "number": attrs.get("Number"),
                    "cycle_time_us": _cycle_value(attrs),
                }
            )
    return tasks


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
        ".sw": "software_configuration",
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
        self.project_metadata_path = self.database_path.parent / "project_metadata.json"

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)").fetchall()
        }
        migrations = {
            "quality": "TEXT NOT NULL DEFAULT 'normal'",
            "verified": "INTEGER NOT NULL DEFAULT 0",
            "deprecated": "INTEGER NOT NULL DEFAULT 0",
            "do_not_copy": "INTEGER NOT NULL DEFAULT 0",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")

    def _load_project_annotations(self) -> dict[str, dict[str, Any]]:
        if not self.project_metadata_path.exists():
            return {}
        try:
            value = json.loads(self.project_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _project_annotation(self, project_name: str) -> dict[str, Any]:
        value = self._load_project_annotations().get(project_name, {})
        if not isinstance(value, dict):
            return {}
        quality = str(value.get("quality", "normal"))
        if quality not in {"gold", "normal", "deprecated"}:
            quality = "normal"
        return {
            "quality": quality,
            "verified": bool(value.get("verified", False)),
            "deprecated": bool(value.get("deprecated", quality == "deprecated")),
            "do_not_copy": bool(value.get("do_not_copy", False)),
            "notes": str(value.get("notes", "")),
        }

    def annotate_project(
        self,
        project: str,
        *,
        quality: str = "normal",
        verified: bool = False,
        deprecated: bool = False,
        do_not_copy: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        if quality not in {"gold", "normal", "deprecated"}:
            raise ValueError("quality must be one of: gold, normal, deprecated")
        if quality == "deprecated":
            deprecated = True
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            row = connection.execute(
                "SELECT name FROM projects WHERE name=? COLLATE NOCASE", (project,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown project: {project}")
        annotations = self._load_project_annotations()
        annotations[project] = {
            "quality": quality,
            "verified": bool(verified),
            "deprecated": bool(deprecated),
            "do_not_copy": bool(do_not_copy),
            "notes": notes,
        }
        self.project_metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_metadata_path.write_text(
            json.dumps(annotations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            connection.execute(
                """UPDATE projects SET quality=?, verified=?, deprecated=?, do_not_copy=?, notes=?
                WHERE name=? COLLATE NOCASE""",
                (quality, int(verified), int(deprecated), int(do_not_copy), notes, project),
            )
        return {"ok": True, "project": project, "metadata_path": str(self.project_metadata_path), **annotations[project]}

    @staticmethod
    def _project_roots(root: Path) -> dict[Path, Path | None]:
        project_files = sorted(root.rglob("*.apj"))
        project_roots: dict[Path, Path | None] = {path.parent.resolve(): path for path in project_files}
        if not project_roots:
            project_roots[root] = None
        return project_roots

    @staticmethod
    def _remove_file_documents(connection: sqlite3.Connection, project_id: int, relative_path: str) -> None:
        ids = connection.execute(
            "SELECT id FROM documents WHERE project_id=? AND relative_path=?",
            (project_id, relative_path),
        ).fetchall()
        connection.executemany("DELETE FROM documents_fts WHERE rowid=?", [(row[0],) for row in ids])
        connection.execute(
            "DELETE FROM documents WHERE project_id=? AND relative_path=?",
            (project_id, relative_path),
        )
        connection.execute(
            "DELETE FROM source_files WHERE project_id=? AND relative_path=?",
            (project_id, relative_path),
        )
        connection.execute(
            "DELETE FROM tasks WHERE project_id=? AND software_path=?",
            (project_id, relative_path),
        )

    def _index_file(
        self,
        connection: sqlite3.Connection,
        path: Path,
        project_id: int,
        project_name: str,
        project_root: Path,
        raw_hash: str,
    ) -> tuple[int, str | None]:
        text, encoding = read_source(path)
        relative_path = path.relative_to(project_root).as_posix()
        origin = origin_for(relative_path)
        units = parse_units(path, text)
        for unit in units:
            digest = hashlib.sha256(unit.content.encode("utf-8", errors="replace")).hexdigest()
            cursor = connection.execute(
                """INSERT INTO documents
                (project_id, relative_path, language, origin, symbol_name, symbol_type,
                 start_line, end_line, encoding, content_hash, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
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
        if path.suffix.lower() == ".sw":
            task_rows = parse_software_tasks(text, relative_path)
            connection.executemany(
                """INSERT INTO tasks
                (project_id, software_path, task_class, task_name, source, language, description, number, cycle_time_us)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        project_id,
                        item["software_path"],
                        item["task_class"],
                        item["task_name"],
                        item["source"],
                        item["language"],
                        item["description"],
                        item["number"],
                        item["cycle_time_us"],
                    )
                    for item in task_rows
                ],
            )
        stat = path.stat()
        connection.execute(
            """INSERT OR REPLACE INTO source_files
            (project_id, relative_path, raw_hash, byte_size, modified_ns, encoding, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, relative_path, raw_hash, stat.st_size, stat.st_mtime_ns, encoding, utc_now()),
        )
        return len(units), encoding

    def rebuild(self, source_root: str | Path) -> dict[str, Any]:
        root = Path(source_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Source root does not exist or is not a directory: {root}")
        project_roots = self._project_roots(root)
        warnings: list[str] = []
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            connection.execute("DELETE FROM documents_fts")
            connection.execute("DELETE FROM documents")
            connection.execute("DELETE FROM tasks")
            connection.execute("DELETE FROM projects")
            project_ids: dict[Path, int] = {}
            for project_root, project_file in project_roots.items():
                metadata = parse_project_file(project_file) if project_file else {}
                annotation = self._project_annotation(project_root.name)
                relative_project_file = (
                    project_file.relative_to(project_root).as_posix() if project_file else None
                )
                cursor = connection.execute(
                    """INSERT INTO projects
                    (name, root_path, project_file, as_version, project_version, description, metadata_json,
                     quality, verified, deprecated, do_not_copy, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_root.name,
                        str(project_root),
                        relative_project_file,
                        metadata.get("as_version"),
                        metadata.get("project_version"),
                        metadata.get("description"),
                        json.dumps(metadata, ensure_ascii=False),
                        annotation["quality"], int(annotation["verified"]), int(annotation["deprecated"]),
                        int(annotation["do_not_copy"]), annotation["notes"],
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
                    raw = path.read_bytes()
                    raw_hash = hashlib.sha256(raw).hexdigest()
                    documents, _encoding = self._index_file(
                        connection,
                        path,
                        project_ids[project_root],
                        project_root.name,
                        project_root,
                        raw_hash,
                    )
                except OSError as exc:
                    warnings.append(f"Could not read {path}: {exc}")
                    continue
                indexed_files += 1
                indexed_documents += documents
            meta = {
                "schema_version": "4",
                "source_root": str(root),
                "indexed_at": utc_now(),
                "tool_version": "0.4.0",
            }
            connection.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", meta.items()
            )
            task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return {
            "ok": True,
            "source_root": str(root),
            "database": str(self.database_path),
            "projects": len(project_ids),
            "files": indexed_files,
            "documents": indexed_documents,
            "tasks": task_count,
            "warnings": warnings[:50],
        }

    def sync(self, source_root: str | Path) -> dict[str, Any]:
        """Synchronize only added, changed and removed source files."""
        root = Path(source_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Source root does not exist or is not a directory: {root}")
        if not self.database_path.exists():
            result = self.rebuild(root)
            result.update({"mode": "rebuild", "added_files": result["files"], "changed_files": 0, "removed_files": 0, "skipped_files": 0})
            return result
        project_roots = self._project_roots(root)
        warnings: list[str] = []
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
            if meta.get("source_root") != str(root):
                result = self.rebuild(root)
                result.update({"mode": "rebuild", "added_files": result["files"], "changed_files": 0, "removed_files": 0, "skipped_files": 0})
                return result
            existing_projects = {
                Path(row["root_path"]): int(row["id"])
                for row in connection.execute("SELECT id, root_path FROM projects")
            }
            project_ids: dict[Path, int] = {}
            for project_root, project_file in project_roots.items():
                metadata = parse_project_file(project_file) if project_file else {}
                annotation = self._project_annotation(project_root.name)
                relative_project_file = project_file.relative_to(project_root).as_posix() if project_file else None
                project_id = existing_projects.get(project_root)
                if project_id is None:
                    cursor = connection.execute(
                        """INSERT INTO projects
                        (name, root_path, project_file, as_version, project_version, description, metadata_json,
                         quality, verified, deprecated, do_not_copy, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (project_root.name, str(project_root), relative_project_file, metadata.get("as_version"),
                         metadata.get("project_version"), metadata.get("description"),
                         json.dumps(metadata, ensure_ascii=False), annotation["quality"], int(annotation["verified"]),
                         int(annotation["deprecated"]), int(annotation["do_not_copy"]), annotation["notes"]),
                    )
                    project_id = int(cursor.lastrowid)
                else:
                    connection.execute(
                        """UPDATE projects SET name=?, project_file=?, as_version=?, project_version=?,
                        description=?, metadata_json=?, quality=?, verified=?, deprecated=?, do_not_copy=?, notes=?
                        WHERE id=?""",
                        (project_root.name, relative_project_file, metadata.get("as_version"),
                         metadata.get("project_version"), metadata.get("description"),
                         json.dumps(metadata, ensure_ascii=False), annotation["quality"], int(annotation["verified"]),
                         int(annotation["deprecated"]), int(annotation["do_not_copy"]), annotation["notes"], project_id),
                    )
                project_ids[project_root] = project_id
            current_keys: set[tuple[int, str]] = set()
            added = changed = skipped = 0
            documents_added = 0
            for path in sorted(root.rglob("*")):
                if not path.is_file() or not _should_index(path, root):
                    continue
                candidates = [candidate for candidate in project_roots if path.is_relative_to(candidate)]
                project_root = max(candidates, key=lambda item: len(item.parts)) if candidates else root
                if project_root not in project_ids:
                    continue
                relative_path = path.relative_to(project_root).as_posix()
                key = (project_ids[project_root], relative_path)
                current_keys.add(key)
                raw = path.read_bytes()
                raw_hash = hashlib.sha256(raw).hexdigest()
                previous = connection.execute(
                    "SELECT raw_hash FROM source_files WHERE project_id=? AND relative_path=?", key
                ).fetchone()
                if previous and previous[0] == raw_hash:
                    skipped += 1
                    continue
                if previous:
                    changed += 1
                else:
                    added += 1
                try:
                    self._remove_file_documents(connection, *key)
                    count, _encoding = self._index_file(
                        connection, path, key[0], project_root.name, project_root, raw_hash
                    )
                    documents_added += count
                except (OSError, UnicodeError) as exc:
                    warnings.append(f"Could not update {path}: {exc}")
            stale = [
                (int(row["project_id"]), row["relative_path"])
                for row in connection.execute("SELECT project_id, relative_path FROM source_files")
                if (int(row["project_id"]), row["relative_path"]) not in current_keys
            ]
            for key in stale:
                self._remove_file_documents(connection, *key)
            for project_root, project_id in list(existing_projects.items()):
                if project_root not in project_ids:
                    connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            meta.update({"schema_version": "4", "source_root": str(root), "indexed_at": utc_now(), "tool_version": "0.4.0"})
            connection.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", meta.items())
            project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return {
            "ok": True, "mode": "sync", "source_root": str(root), "database": str(self.database_path),
            "projects": project_count, "documents": document_count, "tasks": task_count, "documents_added": documents_added,
            "added_files": added, "changed_files": changed, "removed_files": len(stale),
            "skipped_files": skipped, "warnings": warnings[:50],
        }

    def status(self) -> dict[str, Any]:
        if not self.database_path.exists():
            return {"ok": False, "database": str(self.database_path), "indexed": False}
        with closing(self.connect()) as connection, connection:
            self._initialize(connection)
            meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
            projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            tasks = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            files = connection.execute("SELECT COUNT(DISTINCT project_id || ':' || relative_path) FROM documents").fetchone()[0]
            origins = {
                row["origin"]: row["count"]
                for row in connection.execute(
                    "SELECT origin, COUNT(*) AS count FROM documents GROUP BY origin"
                )
            }
            quality_counts = {
                row["quality"]: row["count"]
                for row in connection.execute(
                    "SELECT quality, COUNT(*) AS count FROM projects GROUP BY quality"
                )
            }
            verified_projects = connection.execute(
                "SELECT COUNT(*) FROM projects WHERE verified=1"
            ).fetchone()[0]
        return {
            "ok": True,
            "indexed": bool(meta.get("indexed_at")),
            "database": str(self.database_path),
            "source_root": meta.get("source_root"),
            "indexed_at": meta.get("indexed_at"),
            "projects": projects,
            "files": files,
            "documents": documents,
            "tasks": tasks,
            "documents_by_origin": origins,
            "projects_by_quality": quality_counts,
            "verified_projects": verified_projects,
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
        for key in ("quality", "verified", "deprecated", "do_not_copy", "notes"):
            if key in row.keys():
                value = row[key]
                payload[key] = bool(value) if key in {"verified", "deprecated", "do_not_copy"} else value
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
        quality: str | None = None,
        verified_only: bool = False,
        include_deprecated: bool = False,
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
        if quality:
            filters.append("p.quality = ?")
            parameters.append(quality)
        if verified_only:
            filters.append("p.verified = 1")
        if not include_deprecated and quality != "deprecated":
            filters.append("p.deprecated = 0 AND p.do_not_copy = 0")
        filter_sql = (" AND " + " AND ".join(filters)) if filters else ""
        base_columns = """d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
            d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
            p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes"""
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
            "filters": {"project": project, "origin": origin, "language": language, "quality": quality,
                        "verified_only": verified_only, "include_deprecated": include_deprecated},
            "count": len(rows),
            "results": [
                self._row_payload(row, include_source=include_source, max_chars=max_chars_per_result)
                for row in rows
            ],
        }

    def search_similar(
        self,
        query: str | None = None,
        *,
        reference_document_id: int | None = None,
        project: str | None = None,
        origin: str | None = None,
        language: str | None = None,
        quality: str | None = None,
        verified_only: bool = False,
        include_deprecated: bool = False,
        limit: int = 10,
        include_source: bool = True,
        max_chars_per_result: int = 4000,
    ) -> dict[str, Any]:
        """Find lexical/structural neighbors without pretending to be vector search."""
        self._ensure_index()
        reference: sqlite3.Row | None = None
        with closing(self.connect()) as connection, connection:
            if reference_document_id is not None:
                reference = connection.execute(
                    """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                    d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                    p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                    FROM documents d JOIN projects p ON p.id=d.project_id WHERE d.id=?""",
                    (int(reference_document_id),),
                ).fetchone()
                if reference is None:
                    raise ValueError(f"Unknown document_id: {reference_document_id}")
                query_text = reference["content"]
            else:
                query_text = (query or "").strip()
            if not query_text:
                raise ValueError("query or reference_document_id must be provided")
            tokens = {token.casefold() for token in IDENTIFIER_RE.findall(query_text) if len(token) > 1}
            if not tokens:
                raise ValueError("query did not contain searchable identifiers")
            fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in list(tokens)[:40])
            filters: list[str] = []
            params: list[Any] = [fts_query]
            if project:
                filters.append("p.name = ? COLLATE NOCASE")
                params.append(project)
            if origin and origin != "all":
                filters.append("d.origin = ?")
                params.append(origin)
            if language:
                filters.append("d.language = ?")
                params.append(language)
            if quality:
                filters.append("p.quality = ?")
                params.append(quality)
            if verified_only:
                filters.append("p.verified = 1")
            if not include_deprecated and quality != "deprecated":
                filters.append("p.deprecated = 0 AND p.do_not_copy = 0")
            if reference is not None:
                filters.append("d.id <> ?")
                params.append(int(reference_document_id))
            rows = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                FROM documents_fts f JOIN documents d ON d.id=f.rowid JOIN projects p ON p.id=d.project_id
                WHERE documents_fts MATCH ?""" + (" AND " + " AND ".join(filters) if filters else "") +
                " ORDER BY bm25(documents_fts) LIMIT 500",
                params,
            ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        reference_type = reference["symbol_type"] if reference is not None else None
        reference_language = reference["language"] if reference is not None else None
        for row in rows:
            candidate_tokens = {token.casefold() for token in IDENTIFIER_RE.findall(row["content"]) if len(token) > 1}
            if not candidate_tokens:
                continue
            overlap = len(tokens & candidate_tokens)
            union = len(tokens | candidate_tokens)
            score = overlap / union if union else 0.0
            if reference_type and row["symbol_type"] == reference_type:
                score += 0.12
            if reference_language and row["language"] == reference_language:
                score += 0.08
            if row["symbol_type"] in {"program", "action", "function_block", "function", "c_function", "source_file"}:
                score += 0.12
            elif row["symbol_type"] == "variable_block":
                score += 0.03
            elif row["symbol_type"] == "data_type":
                score += 0.05
            elif row["symbol_type"] == "variable":
                score -= 0.08
            if row["origin"] == "user":
                score += 0.02
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["project_name"], item[1]["relative_path"], item[1]["start_line"]))
        results = []
        for score, row in scored[: max(1, min(int(limit), 50))]:
            payload = self._row_payload(row, include_source=include_source, max_chars=max_chars_per_result)
            payload["similarity_score"] = round(score, 6)
            results.append(payload)
        return {
            "ok": True,
            "mode": "lexical_structural",
            "query": query if reference is None else None,
            "reference_document_id": reference_document_id,
            "filters": {"project": project, "origin": origin, "language": language, "quality": quality,
                        "verified_only": verified_only, "include_deprecated": include_deprecated},
            "count": len(results),
            "results": results,
            "note": "Scores use identifier/control-token overlap plus language and symbol-kind boosts; this is not embedding search.",
        }

    def find_symbol(
        self,
        name: str,
        *,
        project: str | None = None,
        symbol_type: str | None = None,
        quality: str | None = None,
        verified_only: bool = False,
        include_deprecated: bool = False,
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
        if quality:
            filters.append("p.quality = ?")
            params.append(quality)
        if verified_only:
            filters.append("p.verified = 1")
        if not include_deprecated and quality != "deprecated":
            filters.append("p.deprecated = 0 AND p.do_not_copy = 0")
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                f"""SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
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
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
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
                d.origin, d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                FROM documents d JOIN projects p ON p.id=d.project_id WHERE d.id=?""",
                (int(document_id),),
            ).fetchone()
            if primary is None:
                raise ValueError(f"Unknown document_id: {document_id}")
            parent = str(Path(primary["relative_path"]).parent).replace("\\", "/")
            prefix = "" if parent == "." else parent + "/"
            related = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE d.project_id=? AND d.id<>? AND d.relative_path LIKE ?
                ORDER BY CASE d.symbol_type
                    WHEN 'variable_block' THEN 0 WHEN 'data_type' THEN 1
                    WHEN 'program' THEN 2 WHEN 'action' THEN 3 ELSE 4 END,
                    d.relative_path, d.start_line""",
                (primary["project_id"], int(document_id), prefix + "%"),
            ).fetchall()
            tasks = self._tasks_for_path(connection, int(primary["project_id"]), primary["relative_path"])
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
            "tasks": tasks,
            "context_chars": used,
            "context_truncated": used >= budget,
            "note": "Related context combines directory siblings with B&R Task assignments; it is not a compiler-grade dependency graph.",
        }

    @staticmethod
    def _tasks_for_path(
        connection: sqlite3.Connection, project_id: int, relative_path: str
    ) -> list[dict[str, Any]]:
        parts = list(Path(relative_path).parts)
        try:
            logical_index = next(index for index, part in enumerate(parts) if part.casefold() == "logical")
            module_parts = parts[logical_index + 1 : -1]
        except StopIteration:
            module_parts = parts[:-1]
        module_parts = [part for part in module_parts if part.casefold() not in {"code", "sources"}]
        file_stem = Path(parts[-1]).stem if parts else ""
        candidates: list[str] = []
        if file_stem:
            candidates.append(".".join([*module_parts, file_stem]).casefold())
        for end in range(len(module_parts), 0, -1):
            candidates.append(".".join(module_parts[:end]).casefold())
        rows = connection.execute(
            """SELECT task_class, task_name, source, software_path, language, description, number, cycle_time_us
            FROM tasks WHERE project_id=? ORDER BY task_class, task_name""",
            (project_id,),
        ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            source_stem = Path(row["source"]).stem.casefold()
            if any(source_stem == candidate or source_stem.endswith("." + candidate) for candidate in candidates):
                matches.append(dict(row))
        return matches

    def get_task_configuration(
        self, project: str, *, task_name: str | None = None, source: str | None = None
    ) -> dict[str, Any]:
        self._ensure_index()
        clauses = ["p.name=? COLLATE NOCASE"]
        params: list[Any] = [project]
        if task_name:
            clauses.append("t.task_name=? COLLATE NOCASE")
            params.append(task_name)
        if source:
            clauses.append("t.source LIKE ? COLLATE NOCASE")
            params.append(f"%{source}%")
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                """SELECT t.task_class, t.task_name, t.source, t.software_path, t.language,
                t.description, t.number, t.cycle_time_us, p.name AS project,
                p.quality, p.verified, p.deprecated, p.do_not_copy
                FROM tasks t JOIN projects p ON p.id=t.project_id
                WHERE """ + " AND ".join(clauses) +
                " ORDER BY t.software_path, t.task_class, t.task_name LIMIT 500",
                params,
            ).fetchall()
        return {
            "ok": True,
            "project": project,
            "count": len(rows),
            "tasks": [
                {
                    **dict(row),
                    "verified": bool(row["verified"]),
                    "deprecated": bool(row["deprecated"]),
                    "do_not_copy": bool(row["do_not_copy"]),
                }
                for row in rows
            ],
            "cycle_time_note": "Only explicit cycle/period attributes are reported; missing values remain null.",
        }

    def get_type_definition(self, type_name: str, *, project: str | None = None) -> dict[str, Any]:
        self._ensure_index()
        clauses = ["d.symbol_name=? COLLATE NOCASE", "d.symbol_type='data_type'"]
        params: list[Any] = [type_name]
        if project:
            clauses.append("p.name=? COLLATE NOCASE")
            params.append(project)
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE """ + " AND ".join(clauses) + " ORDER BY p.name, d.relative_path",
                params,
            ).fetchall()
        return {
            "ok": True,
            "type_name": type_name,
            "count": len(rows),
            "definitions": [self._row_payload(row, include_source=True, max_chars=30000) for row in rows],
        }

    def find_references(
        self, name: str, *, project: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        self._ensure_index()
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        clauses = ["d.content LIKE ?"]
        params: list[Any] = [f"%{name}%"]
        if project:
            clauses.append("p.name=? COLLATE NOCASE")
            params.append(project)
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                """SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
                d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
                p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
                FROM documents d JOIN projects p ON p.id=d.project_id
                WHERE """ + " AND ".join(clauses) + " ORDER BY p.name, d.relative_path, d.start_line LIMIT 500",
                params,
            ).fetchall()
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", re.IGNORECASE)
        references: list[dict[str, Any]] = []
        for row in rows:
            for offset, line in enumerate(row["content"].splitlines()):
                if pattern.search(line):
                    references.append(
                        {
                            "document_id": row["id"],
                            "project": row["project_name"],
                            "path": row["relative_path"],
                            "language": row["language"],
                            "origin": row["origin"],
                            "symbol": row["symbol_name"],
                            "symbol_type": row["symbol_type"],
                            "line": row["start_line"] + offset,
                            "text": line.strip()[:500],
                            "quality": row["quality"],
                            "verified": bool(row["verified"]),
                            "deprecated": bool(row["deprecated"]),
                            "do_not_copy": bool(row["do_not_copy"]),
                        }
                    )
                    if len(references) >= max(1, min(int(limit), 500)):
                        break
            if len(references) >= max(1, min(int(limit), 500)):
                break
        return {"ok": True, "name": name, "count": len(references), "references": references}

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
            task_rows = [
                {
                    "task_class": item["task_class"],
                    "task_name": item["task_name"],
                    "source": item["source"],
                    "software_path": item["software_path"],
                    "language": item["language"],
                    "description": item["description"],
                    "number": item["number"],
                    "cycle_time_us": item["cycle_time_us"],
                }
                for item in connection.execute(
                    """SELECT task_class, task_name, source, software_path, language, description, number, cycle_time_us
                    FROM tasks WHERE project_id=? ORDER BY software_path, task_class, task_name LIMIT 500""",
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
            "quality": row["quality"],
            "verified": bool(row["verified"]),
            "deprecated": bool(row["deprecated"]),
            "do_not_copy": bool(row["do_not_copy"]),
            "notes": row["notes"],
            "metadata_path": str(self.project_metadata_path),
            "metadata": json.loads(row["metadata_json"]),
            "documents_by_type": type_counts,
            "documents_by_language": language_counts,
            "top_level_paths": top_paths,
            "task_count": len(task_rows),
            "tasks": task_rows,
        }
