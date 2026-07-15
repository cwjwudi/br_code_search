"""Reproducible retrieval-quality evaluation for the B&R code index."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from .core import CodeSearchIndex


SUPPORTED_OPERATIONS = {"search", "similar", "find_symbol"}
DEFAULT_CUTOFFS = (1, 3, 5, 10)


def _load_dataset(path: Path) -> tuple[int, list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read evaluation dataset {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Evaluation dataset is not valid JSON: {exc}") from exc
    if isinstance(raw, list):
        version, cases = 1, raw
    elif isinstance(raw, dict):
        version = int(raw.get("version", 1))
        cases = raw.get("queries", raw.get("cases"))
    else:
        raise ValueError("Evaluation dataset must be an object or a list of cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation dataset must contain a non-empty 'queries' list")
    normalized: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise ValueError(f"Evaluation case {index} must be an object")
        operation = str(case.get("operation", "search"))
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError(
                f"Evaluation case {index} has unsupported operation {operation!r}; "
                f"choose one of {sorted(SUPPORTED_OPERATIONS)}"
            )
        query = case.get("query", case.get("name"))
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"Evaluation case {index} must provide a non-empty query/name")
        relevant = case.get("relevant", case.get("expected"))
        if isinstance(relevant, (str, dict)):
            relevant = [relevant]
        if not isinstance(relevant, list) or not relevant:
            raise ValueError(f"Evaluation case {index} must provide a non-empty relevant list")
        filters = case.get("filters", {})
        if not isinstance(filters, dict):
            raise ValueError(f"Evaluation case {index} filters must be an object")
        case_top_k = case.get("top_k")
        if case_top_k is not None and (not isinstance(case_top_k, int) or case_top_k < 1):
            raise ValueError(f"Evaluation case {index} top_k must be a positive integer")
        normalized.append(
            {
                "id": str(case.get("id", f"case-{index}")),
                "operation": operation,
                "query": query,
                "filters": dict(filters),
                "relevant": relevant,
                "top_k": case_top_k,
                "notes": str(case.get("notes", "")),
            }
        )
    return version, normalized


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list):
        if isinstance(expected, list):
            return all(any(_value_matches(item, wanted) for item in actual) for wanted in expected)
        return any(_value_matches(item, expected) for item in actual)
    if isinstance(expected, list):
        return any(_value_matches(actual, item) for item in expected)
    if actual is None:
        return expected is None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    return str(actual).casefold() == str(expected).casefold()


def _result_matches(result: dict[str, Any], expected: Any) -> bool:
    if isinstance(expected, str):
        return _value_matches(result.get("path"), expected)
    if not isinstance(expected, dict):
        return False
    for key, wanted in expected.items():
        if key.endswith("_glob"):
            actual = result.get(key[:-5])
            if isinstance(actual, list):
                if not any(fnmatch.fnmatchcase(str(item), str(wanted)) for item in actual):
                    return False
            elif not fnmatch.fnmatchcase(str(actual or ""), str(wanted)):
                return False
            continue
        if not _value_matches(result.get(key), wanted):
            return False
    return True


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "document_id",
        "project",
        "path",
        "symbol",
        "symbol_type",
        "language",
        "origin",
        "similarity_score",
        "target_cpu_models",
        "target_ar_versions",
        "target_configurations",
    )
    return {key: result[key] for key in keys if key in result}


def _run_case(index: CodeSearchIndex, case: dict[str, Any], default_top_k: int) -> dict[str, Any]:
    top_k = max(1, min(int(case.get("top_k") or default_top_k), 50))
    filters = dict(case["filters"])
    filters.pop("limit", None)
    filters.pop("max_chars_per_result", None)
    if case["operation"] in {"search", "similar"}:
        filters["include_source"] = False
    if case["operation"] == "search":
        response = index.search(case["query"], limit=top_k, **filters)
    elif case["operation"] == "similar":
        response = index.search_similar(case["query"], limit=top_k, **filters)
    else:
        response = index.find_symbol(case["query"], limit=top_k, **filters)
    results = response.get("results", [])
    relevant = case["relevant"]
    first_rank: int | None = None
    matched: Any = None
    for rank, result in enumerate(results, 1):
        for expected in relevant:
            if _result_matches(result, expected):
                first_rank = rank
                matched = expected
                break
        if first_rank is not None:
            break
    reciprocal_rank = 1 / first_rank if first_rank else 0.0
    return {
        "id": case["id"],
        "operation": case["operation"],
        "query": case["query"],
        "filters": case["filters"],
        "top_k": top_k,
        "hit": first_rank is not None,
        "first_relevant_rank": first_rank,
        "reciprocal_rank": round(reciprocal_rank, 6),
        "matched": matched,
        "relevant": relevant,
        "result_count": len(results),
        "results": [_result_summary(result) for result in results],
        "notes": case["notes"],
    }


def evaluate_dataset(
    index: CodeSearchIndex,
    dataset_path: str | Path,
    *,
    top_k: int = 5,
    max_cases: int | None = None,
) -> dict[str, Any]:
    """Run a versioned retrieval dataset and return Hit@K/MRR metrics."""
    dataset = Path(dataset_path).expanduser().resolve()
    default_top_k = max(1, min(int(top_k), 50))
    version, cases = _load_dataset(dataset)
    if max_cases is not None:
        cases = cases[: max(0, int(max_cases))]
    if not cases:
        raise ValueError("No evaluation cases remain after max_cases was applied")
    case_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for case in cases:
        try:
            case_results.append(_run_case(index, case, default_top_k))
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append({"id": case["id"], "error": str(exc)})
    cutoffs = sorted({cutoff for cutoff in DEFAULT_CUTOFFS if cutoff <= default_top_k} | {default_top_k})
    hit_at_k: dict[str, dict[str, Any]] = {}
    for cutoff in cutoffs:
        eligible = [case for case in case_results if case["top_k"] >= cutoff]
        hits = sum(
            1 for case in eligible if case["first_relevant_rank"] is not None and case["first_relevant_rank"] <= cutoff
        )
        hit_at_k[str(cutoff)] = {
            "hits": hits,
            "evaluated": len(eligible),
            "rate": round(hits / len(eligible), 6) if eligible else None,
        }
    mrr = (
        sum(case["reciprocal_rank"] for case in case_results) / len(case_results)
        if case_results
        else 0.0
    )
    return {
        "ok": not errors,
        "dataset": str(dataset),
        "dataset_version": version,
        "top_k": default_top_k,
        "query_count": len(cases),
        "evaluated_count": len(case_results),
        "error_count": len(errors),
        "hit_at_k": hit_at_k,
        "mrr": round(mrr, 6),
        "errors": errors,
        "cases": case_results,
    }
