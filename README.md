# br-code-search

`br-code-search` is a local, read-only reference-code index for B&R Automation
Studio projects. It indexes B&R source units into SQLite/FTS5 and exposes them
to AI clients through an independent stdio MCP server.

Current release: `0.12.0`.

The reference repository is never modified. Generated indexes are written to
this tool's `var/` directory by default.

## Indexed formats

- IEC Structured Text: `.st`, `.fun`
- Declarations and types: `.var`, `.typ`
- ANSI C: `.c`, `.h`
- C++: `.cpp`, `.cc`, `.hpp`
- Python and configuration: `.py`, `.json`, `.yaml`, `.yml`, `.xml`
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
python -m br_code_search.cli source-provenance
python -m br_code_search.cli embedding-status --backend hashing
python -m br_code_search.cli qdrant-status
python -m br_code_search.cli qdrant-export --path var/qdrant --collection br_code_search --max-documents 50000
python -m br_code_search.cli qdrant-search "timeout reset alarm" --path var/qdrant --limit 5
python -m br_code_search.cli toolchain-status
python -m br_code_search.cli import-toolchain-report C:\path\to\build-report.json --project ProjectName
python -m br_code_search.cli build-diagnostic-summary
python -m br_code_search.cli record-validation ProjectName --kind build --status passed --source "Automation Studio"
python -m br_code_search.cli compile-history ProjectName
python -m br_code_search.cli search-build-errors "E123"
python -m br_code_search.cli search MpAxisBasic --origin user
python -m br_code_search.cli find-symbol fbHomeMaster
python -m br_code_search.cli similar "timeout reset alarm cylinder" --origin user
python -m br_code_search.cli similar-function-block MpAxisBasic --no-source
python -m br_code_search.cli library-usage mapp
python -m br_code_search.cli architecture ProjectName
python -m br_code_search.cli search MpAxisBasic --as-version 4.12 --ar-version H4.93 --cpu-model X20CP1686X --library mapp6D
python -m br_code_search.cli search fbMpAbMasterCalc --aggregate-files --limit 5
python -m br_code_search.cli tasks "2406长虹飞狮"
python -m br_code_search.cli type MC_ACP_ENCOD_REF
python -m br_code_search.cli references Ready --limit 20
python -m br_code_search.cli impact Ready --limit 50
python -m br_code_search.cli annotate-project "2406长虹飞狮" --quality gold --verified --notes "现场验证通过"
python -m br_code_search.cli search MpAxisBasic --quality gold --verified-only --origin user
python -m br_code_search.cli evaluate eval/retrieval_queries.json --top-k 5
python -m br_code_search.cli hybrid "fault restart timeout" --backend hashing --limit 5
python -m br_code_search.cli compare 123 456 --max-chars 10000
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
- `br_get_source_provenance`: inspect read-only Git revision/branch/dirty state for the indexed source root
- `br_get_embedding_status`: check optional local embedding runtime availability
- `br_get_qdrant_status`: check optional Qdrant client availability
- `br_export_qdrant`: export cached vectors and B&R metadata to local or remote Qdrant
- `br_search_qdrant`: query an explicitly configured Qdrant collection and hydrate source from SQLite
- `br_get_toolchain_status`: inspect the sibling B&R toolchain repository without executing PLC commands
- `br_import_toolchain_report`: import a JSON report produced by the registered `br-plc-toolchain` MCP into build history
- `br_record_project_validation`: record external build/field/version feedback outside the source repository
- `br_get_compile_history`: return recorded build results and diagnostics for a project
- `br_search_build_errors`: search recorded build errors/warnings across projects
- `br_get_build_diagnostic_summary`: aggregate repeated build errors, warnings and statuses across projects
- `br_evaluate_retrieval`: run a versioned JSON query set and report Hit@K/MRR
- `br_annotate_project`: persist project quality and verification metadata outside the source repository
- `br_search_code`: full-text and exact source search
- `br_find_similar_code`: lightweight lexical/structural neighbor search
- `br_find_similar_function_block`: compare only `FUNCTION_BLOCK` implementations
- `br_get_library_usage`: find project and source-unit usage of a technology library
- `br_get_project_architecture`: summarize modules, tasks, symbols, targets and validation
- `br_search_hybrid`: combine optional local vectors with lexical/structural ranking
- `br_find_symbol`: exact or prefix symbol lookup
- `br_get_symbol`: retrieve one indexed source unit by document id
- `br_get_program_context`: retrieve a source unit with bounded sibling context
- `br_get_project_overview`: summarize one indexed Automation Studio project
- `br_get_task_configuration`: retrieve `.sw` TaskClass/Task assignments and explicit cycle attributes
- `br_get_type_definition`: retrieve indexed `TYPE` declarations
- `br_find_references`: return whole-identifier, line-level references with declaration/use and read/write/call/member access classification
- `br_get_symbol_impact`: summarize affected documents/projects, access directions, callers and target coverage
- `br_compare_implementations`: compare two indexed units with provenance and a bounded unified diff

`br_search_code`, `br_find_similar_code` and `br_find_symbol` accept optional
`as_version`, `ar_version`, `cpu_model`, `library` and `library_version` filters.
`br_search_code` and the CLI `search` command also accept `aggregate_files` /
`--aggregate-files` to group multiple parsed units from one source file while
retaining their symbol and unit metadata.
`br_get_task_configuration` and CLI `tasks` can additionally filter by the
exact task CPU model and Automation Runtime version discovered from its nearby
`Cpu.pkg`.
Document results also expose `target_cpu_models`, `target_ar_versions` and
`target_configurations`. CPU/AR filters use these target associations when a
source unit belongs to a specific physical target, while shared logical units
remain eligible when no target assignment can be inferred.

`br_get_program_context` is the preferred tool before an AI writes code. It
returns the matched source plus related `Init`, `Cyclic`, `Exit`, action,
variable and type files from the same module directory, and any matching `.sw`
Task assignment, within a caller-defined character budget. It also returns
declarations extracted from VAR blocks and type-reference matches against local
or library `TYPE`/`FUNCTION_BLOCK` symbols.

`br_get_source_provenance` reports a Git revision when the source root is a
checkout. A plain directory such as the current three-project corpus is
explicitly reported as path/time based rather than being treated as versioned.
`br_get_symbol_impact` is an index-level reference summary; it is not a
compiler-grade data-flow or safety analysis.

## Current limits

This version performs lexical search, incremental synchronization, tolerant structural parsing,
basic `.sw` TaskClass/Task extraction, VAR declaration/type resolution and line-level identifier references. It
supports AS/AR/CPU/technology-package filters, quality-aware ranking and an
optional hybrid vector backend, but does not claim compiler-grade AST accuracy
or complete cross-reference analysis. The default hashing vector backend is an
offline baseline rather than trained semantic language understanding. Cycle values are returned only when an explicit cycle/period
attribute exists in the source configuration. Parse fallbacks are exposed
as ordinary file units so source remains searchable even when a dialect is not
recognized.

External build, field-verification and compatibility records are stored outside
the source tree. They are returned by project/result APIs and can contribute a
small, explicit boost or penalty to lexical/structural similarity ranking.

The toolchain adapter is intentionally read-only. `br_get_toolchain_status` only
checks the expected `br_device_autodev` documentation/configuration layout, while
`br_import_toolchain_report` consumes a JSON report already produced by the
registered `br-plc-toolchain` MCP. It never launches Automation Studio,
PVITransfer, PowerShell, or PLC writes; build/download permissions remain in the
separate toolchain service.

Imported records retain compact report provenance (`schema_version`, event or
operation id, target/config, log paths and next actions). Use
`br_get_build_diagnostic_summary` or CLI `build-diagnostic-summary` to see
repeated diagnostics without returning full PLC logs.

For multi-target Automation Studio projects, target metadata is inferred from
the nearest `Cpu.pkg`/`Config.pkg` and from `.sw` Task-to-program assignments.
This is path and configuration metadata, not a compiler-grade build graph.

The original similarity tool is deliberately labeled `lexical_structural`: it
uses identifier/control-token overlap with language and symbol-kind boosts.
Use `br_search_hybrid` when vector signals are desired.

Project quality annotations are stored beside the index at
`var/project_metadata.json`. Supported quality values are `gold`, `normal` and
`deprecated`; search defaults exclude projects marked `deprecated` or
`do_not_copy`, and `verified_only=true` restricts results to explicitly
verified projects.

Retrieval evaluation datasets use path/symbol labels rather than copying source
code. Run the bundled baseline with `br_code_search.cli evaluate`; the same
dataset can be passed to the MCP `br_evaluate_retrieval` tool. The report
includes per-case first-hit rank, Hit@1/3/5/10 (where evaluated) and MRR.

Hybrid retrieval defaults to the dependency-free `hashing` backend. It is a
deterministic offline vector baseline with B&R control-vocabulary expansion,
not a trained language model. For a real local embedding model, install the
optional dependency and pass a local model name or path:

```powershell
python -m pip install -e ".[semantic]"
python -m br_code_search.cli hybrid "fault restart timeout" `
  --backend sentence_transformers `
  --model C:\models\your-local-model
```

Vectors are cached in the index by document content hash and backend key, so
unchanged documents are not re-encoded on later calls.

Qdrant is an optional external vector sink. Install it with
`python -m pip install -e ".[qdrant]"`; the export stores vectors and retrieval
metadata while source text remains in SQLite. Local Qdrant mode is convenient
for smoke tests; use a Qdrant server URL for large production collections.
`br_search_qdrant`/CLI `qdrant-search` queries that collection and returns
Qdrant scores while hydrating authoritative source text and validation metadata
from SQLite.
