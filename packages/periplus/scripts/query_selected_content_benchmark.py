"""Frozen-snapshot staged query pair using the shared result/measurement bench.

Receives the ordinary reader environment. Reports no SQL, parameters or result rows.
Each candidate measurement includes key selection; no cross-snapshot key cache.
"""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
from time import monotonic

from periplus.query import benchmarking as bench
from periplus.query.selected_content import selected_content
from periplus.query.prose_matches import prose_matches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-root', type=Path, default=Path(__file__).resolve().parents[3] / 'benchmarks/query/cases')
    parser.add_argument('--case', required=True)
    parser.add_argument('--rule', choices=['selected-content', 'prose-matches'], default='selected-content')
    parser.add_argument('--candidate-first', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    case = bench.discover_cases(args.case_root)[args.case]
    selected = (prose_matches if args.rule == 'prose-matches' else selected_content)(case.sql)
    if selected is None:
        raise SystemExit('Case is not eligible for the selected execution rule')
    connection = bench._connection(case)
    report = {'measurements': {}, 'complete': False}
    try:
        connection.execute('BEGIN TRANSACTION')
        alias = bench.catalogue_config_from_env().alias
        installed = dict(connection.execute("SELECT view_name,sql FROM duckdb_views() WHERE database_name=? AND schema_name='public_v1'", [alias]).fetchall())
        if not selected.matches(connection, installed):
            raise ValueError('Catalogue mismatch')
        order = ['candidate', 'baseline'] if args.candidate_first else ['baseline', 'candidate']
        for label in order:
            with bench.deadline(connection, case.seconds):
                started = monotonic()
                if label == 'candidate':
                    collected = selected.select(connection)
                    if collected is None:
                        raise ValueError('Intermediate collection bound exceeded')
                    if args.rule == 'prose-matches':
                        bindings = collected
                        selected_count = len(collected['1'])
                    else:
                        bindings = {**selected.parameters, selected.key_parameter: collected}
                        selected_count = len(collected)
                    selection_ms = (monotonic() - started) * 1000
                    remaining = case.seconds - selection_ms / 1000
                    if remaining < 1:
                        raise TimeoutError('Selection exhausted benchmark budget')
                    m = asdict(bench._measure(connection, replace(case, sql=selected.sql, seconds=int(remaining)), None, 0,
                        bound_parameters=bindings))
                    m['normal_ms'] += selection_ms
                    m['selection_ms'] = selection_ms
                    m['selected_input_rows'] = selected_count
                    m['within_time_budget'] = m['normal_ms'] <= case.max_warm_ms
                else:
                    m = asdict(bench._measure(connection, case, None, 0))
                report['measurements'][label] = m
        comparisons, failures = bench.compare_reports(
            {'measurements': [report['measurements']['baseline']]},
            {'measurements': [report['measurements']['candidate']]})
        report.update(complete=True, comparisons=comparisons, failures=failures)
    except Exception as exc:
        report['error'] = getattr(exc, 'error_type', type(exc).__name__)
    finally:
        connection.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'complete': report['complete'], 'error': report.get('error'), 'failures': report.get('failures', [])}))
    if not report['complete'] or report.get('failures'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
