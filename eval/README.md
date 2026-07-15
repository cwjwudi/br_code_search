# Retrieval evaluation

`retrieval_queries.json` is a small, versioned smoke set for the supplied B&R
reference corpus. It stores only query text, filters and expected path/symbol
labels; it does not copy source code into the repository.

Run it after indexing the reference repository:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m br_code_search.cli evaluate eval/retrieval_queries.json --top-k 5
```

The evaluator supports `search`, `similar`, `find_symbol` and `hybrid` cases. A relevant
label can match `path`, `project`, `symbol`, `symbol_type`, target metadata or
their `*_glob` forms. Keep labels stable and update the dataset version when
the intended retrieval target changes.
