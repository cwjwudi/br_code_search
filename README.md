# br-code-search

`br-code-search` is a local, read-only reference-code index for B&R Automation
Studio projects. It indexes B&R source units into SQLite/FTS5 and exposes them
to AI clients through an independent stdio MCP server.

The reference repository is never modified. Generated indexes are written to
this tool's `var/` directory by default.

## Indexed formats

- IEC Structured Text: `.st`, `.fun`
- Declarations and types: `.var`, `.typ`
- ANSI C: `.c`, `.h`
- B&R project/package metadata: `.apj`, `.pkg`

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

`br_get_program_context` is the preferred tool before an AI writes code. It
returns the matched source plus related `Init`, `Cyclic`, `Exit`, action,
variable and type files from the same module directory, within a caller-defined
character budget.

## Current limits

This version performs lexical search, incremental synchronization and tolerant structural parsing. It
does not claim compiler-grade AST accuracy, semantic/vector search, complete
cross-reference analysis, or task-cycle extraction. Parse fallbacks are exposed
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
