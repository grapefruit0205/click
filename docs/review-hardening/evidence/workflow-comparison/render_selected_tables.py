#!/usr/bin/env python3
"""Render selected Phase 6 fixture metrics from the completed comparison JSON.
Read-only analysis of existing results; no benchmarks or product changes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

WORKLOADS = (('short', 1), ('heavy', 40_000))
CONFIGURATIONS = ('click-default', 'explicit-reuse')
VERSIONS = ('frozen', 'current')
SELECTED = (
    ('First setup', 'workflow.{config}.setup_ms'),
    ('First validation request', 'stage.{config}.first-run.request_wall_ms'),
    ('Prepared repeat: unchanged', 'stage.{config}.unchanged.request_wall_ms'),
    ('Unrelated change request', 'stage.{config}.unrelated-code.request_wall_ms'),
    ('Related change request', 'stage.{config}.related-code.request_wall_ms'),
    ('Expected failure request', 'stage.{config}.failure.request_wall_ms'),
    ('Retry after failure request', 'stage.{config}.retry.request_wall_ms'),
    ('All transition components', 'workflow.{config}.transition_ms'),
    ('All validation requests, including failure', 'workflow.{config}.validation_wall_ms'),
    ('Additional same-state full audits', 'workflow.{config}.audit_wall_ms'),
    ('Workflow component subtotal, audits excluded', 'workflow.{config}.component_subtotal_excluding_audits_ms'),
    ('Workflow component subtotal, audits included', 'workflow.{config}.component_subtotal_including_audits_ms'),
)


def distribution(values):
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError('Selected metric contains a non-finite/non-numeric value.')
    return {
        'n': len(values), 'samples': values,
        'median': statistics.median(values) if values else None,
        'min': min(values) if values else None,
        'max': max(values) if values else None,
        'sample_stdev': statistics.stdev(values) if len(values) > 1 else None,
        'sample_variance': statistics.variance(values) if len(values) > 1 else None,
    }


def selected_row(result, workload, configuration, label, metric):
    selected = [run for run in result['runs']
                if run['workload'] == workload and not run['outer_warmup'] and run.get('valid')]
    distributions = {}
    for version in VERSIONS:
        values = [run['metrics'][metric] for run in selected if run['version'] == version]
        distributions[version] = distribution(values)
        matches = [item for item in result['metric_distributions']
                   if item['workload'] == workload and item['version'] == version and item['metric'] == metric]
        if values and (len(matches) != 1 or matches[0]['samples'] != values):
            raise ValueError(f'Aggregate/raw metric inconsistency: {workload}/{version}/{metric}')
    # Recompute exact same-outer-trial pairs; do not pair sorted distributions.
    paired = []
    excluded_trials = []
    for trial in (1, 2, 3):
        members = {run['version']: run for run in selected if run['outer_trial'] == trial}
        if set(members) != set(VERSIONS):
            excluded_trials.append(trial)
            continue
        before, after = members['frozen']['metrics'][metric], members['current']['metrics'][metric]
        paired.append({'outer_trial': trial, 'before_ms': before, 'after_ms': after,
                       'delta_ms': before - after,
                       'delta_percent': 100 * (before - after) / before if before > 0 else None})
    declared = [pair for pair in result['paired_source_differences']
                if pair['workload'] == workload and pair.get('metric') == metric and pair['valid_pair']]
    if [pair['delta_ms'] for pair in declared] != [pair['delta_ms'] for pair in paired]:
        raise ValueError(f'Aggregate/raw paired difference inconsistency: {workload}/{metric}')
    deltas = [pair['delta_ms'] for pair in paired]
    return {
        'workload': workload, 'configuration': configuration, 'label': label, 'metric': metric,
        **distributions, 'paired_delta_ms': distribution(deltas),
        'paired_delta_percent': distribution([pair['delta_percent'] for pair in paired if pair['delta_percent'] is not None]),
        'paired_signs': {'positive_current_lower': sum(value > 0 for value in deltas),
                         'equal': sum(value == 0 for value in deltas),
                         'negative_current_higher': sum(value < 0 for value in deltas)},
        'pairs': paired, 'excluded_outer_trials': excluded_trials,
    }


def show(value):
    return '—' if value is None else f'{value:,.3f}'


def interval(value):
    return f"{show(value['median'])} [{show(value['min'])}, {show(value['max'])}]"


def table(rows):
    lines = [
        '| Component (ms) | Before median [min, max] | After median [min, max] | SD before / after | Variance before / after (ms²) | n before / after | Paired Δ median [min, max] | Pair signs + / 0 / − |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for row in rows:
        before, after, signs = row['frozen'], row['current'], row['paired_signs']
        lines.append(f"| {row['label']} | {interval(before)} | {interval(after)} | "
                     f"{show(before['sample_stdev'])} / {show(after['sample_stdev'])} | "
                     f"{show(before['sample_variance'])} / {show(after['sample_variance'])} | "
                     f"{before['n']} / {after['n']} | {interval(row['paired_delta_ms'])} | "
                     f"{signs['positive_current_lower']} / {signs['equal']} / {signs['negative_current_higher']} |")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--markdown-output', required=True, type=Path)
    parser.add_argument('--json-output', required=True, type=Path)
    args = parser.parse_args()
    if args.markdown_output.exists() or args.json_output.exists():
        raise ValueError('Output already exists; original analysis files will not be overwritten.')
    result = json.loads(args.input.read_text(encoding='utf-8'))
    rows, cli_rows, outcomes = [], [], []
    for workload, _ in WORKLOADS:
        for configuration in CONFIGURATIONS:
            for label, template in SELECTED:
                rows.append(selected_row(result, workload, configuration, label, template.format(config=configuration)))
        cli_rows.append(selected_row(result, workload, None, f'{workload}: entire benchmark subprocess', 'subprocess.overall_ms'))
    for run in result['runs']:
        if run.get('valid') and not run['outer_warmup']:
            for outcome in run['stage_outcomes']:
                if outcome['configuration'] in CONFIGURATIONS:
                    outcomes.append({'run_id': run['run_id'], 'workload': run['workload'],
                                     'version': run['version'], 'outer_trial': run['outer_trial'], **outcome})
    output = {
        'input': str(args.input.resolve()),
        'all_16_valid': result['all_16_valid'], 'valid_runs': result['valid_runs'], 'invalid_runs': result['invalid_runs'],
        'selected_rows': rows, 'benchmark_subprocess_rows': cli_rows,
        'measured_stage_outcomes': outcomes, 'scopes': result['scopes'],
        'statistics': 'Median/range, sample standard deviation and sample variance. Warmup excluded. Pairs match exact outer trial. Positive frozen-minus-current means current lower; negative means current higher. No statistical significance claim.',
    }
    lines = ['# Phase 6 selected paired fixture measurements', '',
             f"Input: `{args.input.resolve()}`. Valid runs: {result['valid_runs']}; invalid runs: {result['invalid_runs']}. All sixteen valid: {result['all_16_valid']}.", '',
             'Every value below is in milliseconds. Before is the frozen Phase 0 source; after is the final measured source. '
             'Each valid source/workload has three measured outer trials; the separate warmup is excluded. '
             'Δ = before − after, so negative values mean the after version took longer. SD and variance use sample formulas (n−1). '
             'These are small descriptive samples, with no significance or latency guarantee.', '',
             'The workload names identify 1 versus 40,000 PBKDF rounds per synthetic test. The word “heavy” is the harness label; '
             'it does not establish that this workload amortizes Click setup or models a large production suite. '
             'Both Guarded arms run scripted temporary-fixture approvals; no human decision time or user development task is measured.', '',
             'Request rows retain their first/changed/prepared-repeat boundaries. Workflow component subtotals retain setup, '
             'transitions and all validation requests, including the expected failing step; the audit-inclusive row adds '
             'separate same-state audit cost. These subtotals omit untimed fixture bookkeeping. '
             'The no-Click baseline arm is not subtotaled because its audit field overlaps its parent validation. '
             'No token usage or token-saving percentage was measured.', '']
    for workload, rounds in WORKLOADS:
        for configuration in CONFIGURATIONS:
            lines.extend([f'## {workload} ({rounds:,} rounds) — {configuration}', ''])
            lines.extend(table([row for row in rows if row['workload'] == workload and row['configuration'] == configuration]))
            lines.append('')
    lines.extend(['## Entire benchmark subprocess, separately scoped', '',
                  'This duration includes all three fixture configurations, startup, setup, transitions, actual checks, '
                  'experimental audits, receipt handling and JSON output. It is not an individual verification request or user-task completion time.', ''])
    lines.extend(table(cli_rows))
    lines.extend(['', 'Expected-failure outcomes, execution/reuse counts and audit agreement for every measured stage are retained in the companion JSON. '
                  'All original per-run reports and failed-command logs remain in the input results directory.', ''])
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    with args.markdown_output.open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines))
    with args.json_output.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'markdown': str(args.markdown_output), 'json': str(args.json_output),
                      'selected_rows': len(rows), 'subprocess_rows': len(cli_rows),
                      'all_16_valid': result['all_16_valid']}, ensure_ascii=False))
    return 0 if result['all_16_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
