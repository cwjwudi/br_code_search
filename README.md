# br-code-search

`br-code-search` is a local, read-only reference-code index for B&R Automation
Studio projects. It indexes B&R source units into SQLite/FTS5 and exposes them
to AI clients through an independent stdio MCP server.

Current release: `0.4.3`.

The reference repository is never modified. Generated indexes are written to
this tool's `var/` directory by default.

## Indexed formats

- IEC Structured Text: `.st`, `.fun`
- Declarations and types: `.var`, `.typ`
- ANSI C: `.c`, `.h`
- B&R project/package metadata: `.apj`, `.pkg`, `.sw`

Generated build directories and binary artifacts are ignored. Results retain
the project, relative path, source classification, symbol kind and line range.

## Quick start

From the repository root:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m br_code_search.cli index --source C:\Users\BR\code_base
python -m br_code_search.cli sync --source C:\Users\BR\code_base
python -m br_code_search.cli status
python -m br_code_search.cli search MpAxisBasic --origin user
python -m br_code_search.cli find-symbol fbHomeMaster
python -m br_code_search.cli similar "timeout reset alarm cylinder" --origin user
python -m br_code_search.cli search MpAxisBasic --as-version 4.12 --ar-version H4.93 --cpu-model X20CP1686X --library mapp6D
python -m br_code_search.cli tasks "2406长虹飞狮"
python -m br_code_search.cli type MC_ACP_ENCOD_REF
python -m br_code_search.cli references Ready --limit 20
python -m br_code_search.cli annotate-project "2406长虹飞狮" --quality gold --verified --notes "现场验证通过"
python -m br_code_search.cli search MpAxisBasic --quality gold --verified-only --origin user
```

Start the MCP server:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m br_code_search.mcp_server `
  --source C:\Users\BR\code_base `
  --db "$PWD\var\br_code_search.sqlite3"
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "br-code-search": {
      "type": "stdio",
      "command": "python",
      "args": [
        "-m",
        "br_code_search.mcp_server",
        "--source",
        "C:\\Users\\BR\\code_base",
        "--db",
        "C:\\Users\\BR\\codex_ws\\br_code_search\\var\\br_code_search.sqlite3"
      ],
      "env": {
        "PYTHONPATH": "C:\\Users\\BR\\codex_ws\\br_code_search\\src"
      }
    }
  }
}
```

## MCP tools

- `br_index_codebase`: synchronize or rebuild the local index from the configured source root
- `br_get_index_status`: return index statistics and configured paths
- `br_annotate_project`: persist project quality and verification metadata outside the source repository
- `br_search_code`: full-text and exact source search
- `br_find_similar_code`: lightweight lexical/structural neighbor search
- `br_find_symbol`: exact or prefix symbol lookup
- `br_get_symbol`: retrieve one indexed source unit by document id
- `br_get_program_context`: retrieve a source unit with bounded sibling context
- `br_get_project_overview`: summarize one indexed Automation Studio project
- `br_get_task_configuration`: retrieve `.sw` TaskClass/Task assignments and explicit cycle attributes
- `br_get_type_definition`: retrieve indexed `TYPE` declarations
- `br_find_references`: return whole-identifier, line-level references with declaration/use and read/write/call/member access classification

`br_search_code`, `br_find_similar_code` and `br_find_symbol` accept optional
`as_version`, `ar_version`, `cpu_model`, `library` and `library_version` filters.

`br_get_program_context` is the preferred tool before an AI writes code. It
returns the matched source plus related `Init`, `Cyclic`, `Exit`, action,
variable and type files from the same module directory, and any matching `.sw`
Task assignment, within a caller-defined character budget. It also returns
declarations extracted from VAR blocks and type-reference matches against local
or library `TYPE`/`FUNCTION_BLOCK` symbols.

## Current limits

This version performs lexical search, incremental synchronization, tolerant structural parsing,
basic `.sw` TaskClass/Task extraction, VAR declaration/type resolution and line-level identifier references. It
supports AS/AR/CPU/technology-package filters and quality-aware ranking, but does not claim compiler-grade AST accuracy, semantic/vector search or complete
cross-reference analysis. Cycle values are returned only when an explicit cycle/period
attribute exists in the source configuration. Parse fallbacks are exposed
as ordinary file units so source remains searchable even when a dialect is not
recognized.

The similarity tool is deliberately labeled `lexical_structural`: it uses
identifier/control-token overlap with language and symbol-kind boosts. It is a
stable intermediate step, not a replacement for future embedding search.

Project quality annotations are stored beside the index at
`var/project_metadata.json`. Supported quality values are `gold`, `normal` and
`deprecated`; search defaults exclude projects marked `deprecated` or
`do_not_copy`, and `verified_only=true` restricts results to explicitly
verified projects.
