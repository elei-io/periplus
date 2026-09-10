"""Run the checked-in public-SQL query benchmark scenarios."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from periplus.query.benchmarking import compare_reports, discover_cases, report_payload, measure_pair, environment_metadata, BenchmarkFailure


from periplus.query.content_scope import content_scope


DEFAULT_CASE_ROOT = Path(__file__).resolve().parents[3] / "benchmarks/query/cases"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--case", action="append", dest="case_ids", required=True)
    parser.add_argument("--candidate-first", action="store_true")
    parser.add_argument("--content-scope", action="store_true", help="compare the original SQL with the content-scoping optimization in one snapshot")
    parser.add_argument("--materialize-inputs", action="store_true", help="materialize selected HTML primitives in the content-scope candidate")
    parser.add_argument("--heading-driver", action="store_true", help="select exact matching headings before scoping downstream extraction")
    parser.add_argument("--seconds", type=int, choices=range(1, 121))
    parser.add_argument("--scale", type=int, action="append", dest="scales")
    parser.add_argument("--ordinary-warm-runs", action="store_true", help="time ordinary executions instead of EXPLAIN ANALYZE; scan metrics are unavailable")
    parser.add_argument("--warm-runs", type=int, default=2)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--access-path", choices=("local_direct_reader", "cluster_direct_reader"), default="local_direct_reader")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--sql-override",
        type=Path,
        help="research-only SQL for one case; production acceptance still uses query.sql",
    )
    arguments = parser.parse_args()
    if not 0 <= arguments.warm_runs <= 3:
        parser.error("--warm-runs must be between 0 and 3")

    discovered = discover_cases(arguments.case_root)
    requested = arguments.case_ids or list(discovered)
    missing = sorted(set(requested) - set(discovered))
    if missing:
        parser.error("unknown cases: " + ", ".join(missing))
    if (arguments.materialize_inputs or arguments.heading_driver) and not arguments.content_scope:
        parser.error("scope strategy flags require --content-scope")
    if arguments.ordinary_warm_runs and not (arguments.content_scope or arguments.sql_override):
        parser.error("ordinary warm runs require paired mode")
    if arguments.content_scope and arguments.sql_override:
        parser.error("choose --content-scope or --sql-override")
    if (arguments.sql_override or arguments.content_scope) and len(requested) != 1:
        parser.error("--sql-override requires exactly one --case")
    selected = []
    for identifier in requested:
        case = discovered[identifier]
        if arguments.seconds:
            case = replace(case, seconds=arguments.seconds)
        if arguments.scales:
            invalid = sorted(set(arguments.scales) - set(case.scales))
            if invalid:
                parser.error(
                    f"case {identifier} does not define scales: "
                    + ", ".join(str(item) for item in invalid)
                )
            case = replace(
                case,
                scales=tuple(arguments.scales),
            )
        selected.append(case)

    try:
        if arguments.sql_override or arguments.content_scope:
            case = selected[0]
            scope = content_scope(case.sql, materialize_inputs=arguments.materialize_inputs, heading_driver=arguments.heading_driver) if arguments.content_scope else None
            if arguments.content_scope and scope is None:
                raise ValueError("case is not eligible for content scoping")
            candidate = replace(case, sql=scope.sql if scope else arguments.sql_override.read_text(encoding="utf-8").strip())
            pairs = [measure_pair(case, candidate, scale, warm_runs=arguments.warm_runs,
                                 candidate_first=arguments.candidate_first, verify_scope=scope, profile_warm_runs=not arguments.ordinary_warm_runs) for scale in case.scales]
            payload = {"format_version": 2, "measurements": [p["candidate"] for p in pairs],
                       "paired_baseline": [p["baseline"] for p in pairs],
                       "candidate_first": arguments.candidate_first, "environment": environment_metadata(),
                       "baseline_sql": case.sql, "candidate_sql": candidate.sql,
                       "optimization": "content_scope" if arguments.content_scope else "research_sql",
                       "scope_strategy": {"materialize_inputs": arguments.materialize_inputs, "heading_driver": arguments.heading_driver}}
            comparisons, pair_failures = compare_reports(
                {"measurements": payload["paired_baseline"]}, payload)
            for comparison in comparisons:
                ratio = comparison["warm_time_ratio"] if arguments.warm_runs else comparison["normal_time_ratio"]
                comparison["performance_improved"] = ratio is not None and ratio < 1
                if arguments.content_scope and not comparison["performance_improved"]:
                    pair_failures.append(f"no measured execution speedup for {comparison['case']} scale={comparison['scale']}")
            payload["comparison"] = comparisons
            payload["pair_failures"] = pair_failures
        else:
            payload = report_payload(selected, warm_runs=arguments.warm_runs)
    except Exception as exc:
        # Storage errors can contain credential-bearing connection strings.
        payload = {"format_version": 2, "measurements": [],
                   "failures": [exc.error_type if isinstance(exc, BenchmarkFailure) else type(exc).__name__], "complete": False}
        if isinstance(exc, BenchmarkFailure):
            payload["failed_variant"] = exc.variant
            payload["progress"] = exc.progress
            payload["completed_variants"] = exc.completed_variants
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print("Benchmark incomplete; safe failure type recorded in report")
        raise SystemExit(1) from None
    payload["warm_protocol"] = "single_execution" if not arguments.warm_runs else ("ordinary_execution" if arguments.ordinary_warm_runs else "explain_analyze")
    payload["environment"]["access_path"] = arguments.access_path
    payload["sql_override"] = (
        str(arguments.sql_override.resolve()) if arguments.sql_override else None
    )
    failures = payload.get("pair_failures", []) + [
        f"time budget exceeded for {item['case']} scale={item.get('scale')}"
        for item in payload["measurements"]
        if not item["within_time_budget"]
    ]
    if arguments.baseline:
        baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
        comparisons, comparison_failures = compare_reports(baseline, payload)
        if baseline.get("warm_protocol", "explain_analyze") != payload["warm_protocol"]:
            comparison_failures.append("warm execution protocol mismatch")
        payload["comparison"] = comparisons
        failures.extend(comparison_failures)
    payload["failures"] = failures
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"report={arguments.report.resolve()}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
