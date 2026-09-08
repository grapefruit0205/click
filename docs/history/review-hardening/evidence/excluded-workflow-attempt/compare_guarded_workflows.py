#!/usr/bin/env python3
"""Run the existing v4 Guarded fixture against frozen/current source, locally.
This experiment does not edit the product, install dependencies, or publish data.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import time

VERSIONS = ('frozen', 'current')
WORKLOADS = (('short', 1), ('heavy', 40_000))
CONFIGURATIONS = ('baseline', 'click-default', 'explicit-reuse')
STEPS = ('first-run', 'unrelated-code', 'related-code', 'all-code', 'environment', 'failure', 'retry', 'unchanged')
STAGE_GROUPS = {
    'first-validation': ('first-run',),
    'unrelated-change': ('unrelated-code',),
    'related-change': ('related-code',),
    'prepared-repeat': ('unchanged',),
    'expected-failure': ('failure',),
    'retry-after-failure': ('retry',),
    'all-code-change': ('all-code',),
    'environment-change': ('environment',),
}
SCOPES = {
    'subprocess.overall_ms': 'One benchmark CLI process, including Python startup, all three fixture arms, setup, transitions, checks, audits, receipts and JSON generation; not a user development task.',
    'workflow': 'Raw reported per-arm components. Guarded component subtotals omit untimed fixture bookkeeping and human decision time. Expected failure remains in validation costs. Additional audits are experimental cost.',
    'stage': 'Existing raw stage request, source-command, transition and full-audit measurements remain separate. Prepared repeat is only the final unchanged request after prior setup/transitions.',
    'baseline_arm': 'Ordinary no-Click fixture. Its audit_wall_ms includes parent-suite already used as validation; therefore no derived inclusive workflow subtotal is computed for this arm.',
    'version_delta': 'Frozen-source duration minus current-source duration for the same outer trial/workload/metric. Positive means lower observed duration on current source; negative values remain unchanged.',
    'fixture_comparison': 'Existing within-report same-state comparisons are preserved independently of source-version comparison. Only eligible comparisons enter eligible comparison summaries; ineligible raw comparisons remain attached to each run.',
    'tokens_and_user_task': 'Unmeasured. No token totals, token savings, total development time or broad performance claims are derived.',
}


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def distribution(values):
    return {
        'n': len(values), 'samples': values,
        'median': statistics.median(values) if values else None,
        'min': min(values) if values else None,
        'max': max(values) if values else None,
        'sample_stdev': statistics.stdev(values) if len(values) >= 2 else None,
        'sample_variance': statistics.variance(values) if len(values) >= 2 else None,
    }


def source_fingerprint(root):
    # This is exactly the existing benchmark's engine.source_digest construction.
    files = sorted([*(root / 'hooks').glob('*.py'), root / 'benchmarks/incremental_verification.py'])
    digest = hashlib.sha256()
    manifest = {}
    for path in files:
        content_digest = hashlib.sha256(path.read_bytes()).digest()
        name = path.relative_to(root).as_posix()
        digest.update(name.encode() + b'\0' + content_digest)
        manifest[name] = content_digest.hex()
    return {'source_digest': digest.hexdigest(), 'files': manifest}


def declared_max_rounds(root):
    tree = ast.parse((root / 'benchmarks/incremental_verification.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'MAX_WORKLOAD_ROUNDS' for target in node.targets):
            value = ast.literal_eval(node.value)
            if type(value) is int:
                return value
    raise ValueError('Existing benchmark workload maximum could not be identified.')


def load_existing_validator(root):
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('phase6_existing_benchmark', root / 'benchmarks/incremental_verification.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.workflow_report_is_valid


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def report_metrics(report):
    sample, = report['samples']
    if sample['warmup'] or sample['iteration'] != 0:
        raise ValueError('Unexpected internal iteration/warmup; outer harness owns repetition.')
    metrics, stage_outcomes = {}, []
    for configuration in CONFIGURATIONS:
        arm = sample['arms'][configuration]
        prefix = f'workflow.{configuration}'
        for field in ('setup_ms', 'validation_wall_ms', 'transition_ms', 'audit_wall_ms'):
            metrics[f'{prefix}.{field}'] = arm[field]
        if configuration != 'baseline':
            subtotal = arm['setup_ms'] + arm['transition_ms'] + arm['validation_wall_ms']
            metrics[f'{prefix}.component_subtotal_excluding_audits_ms'] = subtotal
            metrics[f'{prefix}.component_subtotal_including_audits_ms'] = subtotal + arm['audit_wall_ms']
        actual_steps = tuple(step['scenario'] for step in arm['steps'])
        if actual_steps != STEPS:
            raise ValueError(f'Unexpected workflow stages for {configuration}: {actual_steps!r}')
        for step in arm['steps']:
            scenario = step['scenario']
            validation = step['validation']
            expected_status = 'failed' if scenario == 'failure' else 'passed'
            expected_exit = validation['exit_code'] != 0 if scenario == 'failure' else validation['exit_code'] == 0
            if validation['status'] != expected_status or not expected_exit or not step['audit_matches']:
                raise ValueError(f'Fixture/audit outcome mismatch: {configuration}/{scenario}')
            for full in step['full_checks'].values():
                if full['status'] != validation['status'] or full['exit_code'] != validation['exit_code']:
                    raise ValueError(f'Full-check mismatch: {configuration}/{scenario}')
            prefix = f'stage.{configuration}.{scenario}'
            metrics[f'{prefix}.request_wall_ms'] = validation['wall_ms']
            metrics[f'{prefix}.transition_ms'] = step['transition_ms']
            metrics[f'{prefix}.additional_full_audit_ms'] = step['additional_full_audit_ms']
            if numeric(validation.get('test_execution_ms')):
                metrics[f'{prefix}.test_execution_ms'] = validation['test_execution_ms']
            for criterion in ('same-shards', 'parent-suite'):
                metrics[f'{prefix}.audit_{criterion}_wall_ms'] = step['full_checks'][criterion]['wall_ms']
            stage_outcomes.append({
                'configuration': configuration, 'scenario': scenario,
                'status': validation['status'], 'exit_code': validation['exit_code'],
                'expected_failure': scenario == 'failure', 'audit_matches': step['audit_matches'],
                'executed_source_count': validation.get('executed_source_count'),
                'reused_source_count': validation.get('reused_source_count'),
                'request_measurement_scope': validation.get('request_measurement_scope'),
                'test_measurement_scope': validation.get('test_measurement_scope'),
            })
    # Do not drop negative observations, expected-failure timing, or ineligible data.
    for comparison in report['comparison_samples']:
        if comparison['eligible']:
            prefix = f"eligible_fixture_comparison.{comparison['configuration']}.{comparison['scenario']}.{comparison['comparison']}"
            for field in ('delta_ms', 'delta_percent'):
                if numeric(comparison[field]):
                    metrics[f'{prefix}.{field}'] = comparison[field]
    if any(not numeric(value) for value in metrics.values()):
        raise ValueError('Non-finite or malformed metric in report.')
    return metrics, stage_outcomes


def run_cli(argv, cwd, environment, stdout_path, stderr_path, timeout):
    started = time.perf_counter_ns()
    timed_out = False
    with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=stdout, stderr=stderr,
                                   start_new_session=os.name == 'posix')
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Clean up this isolated benchmark process group on the measured Linux host.
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            code = process.returncode
    return code, timed_out, (time.perf_counter_ns() - started) / 1_000_000


def aggregate(runs):
    distributions, pairs, fixture_exclusions = [], [], []
    for workload, _ in WORKLOADS:
        for version in VERSIONS:
            selected = [run for run in runs if run['workload'] == workload and run['version'] == version and not run['outer_warmup'] and run.get('valid')]
            keys = sorted({key for run in selected for key in run['metrics']})
            for key in keys:
                values = [run['metrics'][key] for run in selected if key in run['metrics']]
                distributions.append({'workload': workload, 'version': version, 'metric': key, **distribution(values)})
        for trial in range(1, 4):
            selected = {run['version']: run for run in runs if run['workload'] == workload and run['outer_trial'] == trial and run.get('valid')}
            if set(selected) != set(VERSIONS):
                pairs.append({'workload': workload, 'outer_trial': trial, 'valid_pair': False, 'reason': 'missing-or-invalid-version-result'})
                continue
            before, after = selected['frozen']['metrics'], selected['current']['metrics']
            for key in sorted(before.keys() & after.keys()):
                # Signed deltas in existing fixture comparisons are summarized, not converted into speedups again.
                if key.startswith('eligible_fixture_comparison.'):
                    continue
                delta = before[key] - after[key]
                pairs.append({'workload': workload, 'outer_trial': trial, 'valid_pair': True, 'metric': key,
                              'frozen': before[key], 'current': after[key], 'delta_ms': delta,
                              'delta_percent': 100 * delta / before[key] if before[key] > 0 else None})
    pair_summaries = []
    for workload, _ in WORKLOADS:
        keys = sorted({pair['metric'] for pair in pairs if pair['workload'] == workload and pair['valid_pair']})
        for key in keys:
            selected = [pair for pair in pairs if pair['workload'] == workload and pair.get('metric') == key and pair['valid_pair']]
            pair_summaries.append({'workload': workload, 'metric': key,
                                   'delta_ms': distribution([pair['delta_ms'] for pair in selected]),
                                   'delta_percent': distribution([pair['delta_percent'] for pair in selected if pair['delta_percent'] is not None]),
                                   'negative_delta_count': sum(pair['delta_ms'] < 0 for pair in selected)})
    for run in runs:
        if run.get('raw_comparison_samples') is not None:
            fixture_exclusions.append({'run_id': run['run_id'], 'outer_warmup': run['outer_warmup'],
                                       'comparisons': run['raw_comparison_samples']})
    return {'metric_distributions': distributions, 'paired_source_differences': pairs,
            'paired_source_difference_summaries': pair_summaries,
            'raw_fixture_comparisons_including_ineligible': fixture_exclusions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory; existing artifacts are never overwritten.')
    parser.add_argument('--timeout-seconds', type=float, default=900.0)
    parser.add_argument('--plan', action='store_true', help='Print the exact 16-command schedule without running benchmarks or writing artifacts.')
    args = parser.parse_args()
    roots = {'frozen': args.baseline.resolve(strict=True), 'current': args.current.resolve(strict=True)}
    if roots['frozen'] == roots['current']:
        raise ValueError('Baseline and current source roots must differ.')
    if not 0 < args.timeout_seconds <= 3600:
        raise ValueError('Timeout must be positive and at most 3600 seconds per benchmark CLI.')
    python = str(Path(sys.executable).resolve())
    out = args.output_dir.resolve()
    maximums = {version: declared_max_rounds(root) for version, root in roots.items()}
    if any(rounds > maximum for _, rounds in WORKLOADS for maximum in maximums.values()):
        raise ValueError('Requested workload exceeds an existing benchmark source maximum.')
    schedule = []
    for outer_trial in range(4):
        for workload_index, (workload, rounds) in enumerate(WORKLOADS):
            offset = (outer_trial + workload_index) % 2
            version_order = VERSIONS[offset:] + VERSIONS[:offset]
            for position, version in enumerate(version_order):
                run_id = f'{workload}-trial-{outer_trial}-{version}'
                run_dir = out / run_id
                argv = [python, '-B', str(roots[version] / 'benchmarks/incremental_verification.py'),
                        '--guarded-workflow', '--iterations', '1', '--warmups', '0',
                        '--workload-rounds', str(rounds), '--output', str(run_dir / 'raw.json')]
                schedule.append({'run_id': run_id, 'version': version, 'workload': workload, 'rounds': rounds,
                                 'outer_trial': outer_trial, 'outer_warmup': outer_trial == 0,
                                 'order_in_pair': position, 'version_order': list(version_order),
                                 'argv': argv, 'cwd': str(roots[version]), 'directory': str(run_dir)})
    if args.plan:
        print(json.dumps({'benchmark_process_count': len(schedule), 'python': python, 'source_max_rounds': maximums,
                          'schedule': schedule, 'scopes': SCOPES}, ensure_ascii=False, indent=2))
        return 0
    out.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0')
    initial = {version: source_fingerprint(root) for version, root in roots.items()}
    manifest = {'kind': 'click-phase6-paired-source-guarded-fixture', 'started_at': stamp(),
                'environment': {'python': sys.version, 'executable': python, 'platform': platform.platform(),
                                'pythonhashseed': '0', 'os_cache': 'not-flushed', 'parallel_benchmark_processes': 1},
                'outer_warmups_per_workload_and_version': 1, 'measured_trials_per_workload_and_version': 3,
                'internal_iterations': 1, 'internal_warmups': 0, 'benchmark_process_count': len(schedule),
                'source_max_rounds': maximums, 'initial_source_fingerprints': initial,
                'stage_groups': STAGE_GROUPS, 'scopes': SCOPES, 'schedule': schedule,
                'limitations': ['Synthetic two-file fixture, not universal workload or total development time.',
                                '3 measured outer pairs provide small descriptive samples, not confidence intervals.',
                                'Inner configuration order resets with iterations=1; outer source-version order rotates.',
                                'OS cache and scheduler are uncontrolled; run after correctness checks with no competing tests.',
                                'Scripted fixture approvals exclude human decision time.',
                                'No token/fee measurements or conversion.',
                                'Windows/macOS native performance is not inferred from this Linux measurement.']}
    save(out / 'manifest.json', manifest)
    existing_validator = load_existing_validator(roots['frozen'])
    runs = []
    for item in schedule:
        run = dict(item)
        root = roots[run['version']]
        directory = Path(run['directory'])
        directory.mkdir()
        run['started_at'] = stamp()
        before = source_fingerprint(root)
        run['source_digest_before'] = before['source_digest']
        try:
            code, timed_out, elapsed = run_cli(run['argv'], root, environment, directory / 'stdout.log',
                                             directory / 'stderr.log', args.timeout_seconds)
            run.update(exit_code=code, timed_out=timed_out, subprocess_overall_ms=elapsed)
            after = source_fingerprint(root)
            run['source_digest_after'] = after['source_digest']
            if code != 0 or timed_out:
                raise ValueError('benchmark-command-failed-or-timed-out; preserve stdout/stderr and any raw JSON')
            raw = json.loads((directory / 'raw.json').read_text(encoding='utf-8'))
            if not existing_validator(raw):
                raise ValueError('Existing workflow_report_is_valid rejected the report.')
            if raw['conditions']['iterations'] != 1 or raw['conditions']['warmups'] != 0 or raw['conditions']['workload_rounds'] != run['rounds']:
                raise ValueError('Raw workload conditions differ from schedule.')
            if raw['environment']['python'] != platform.python_version():
                raise ValueError('Benchmark used an unexpected interpreter version.')
            if not (before['source_digest'] == after['source_digest'] == initial[run['version']]['source_digest'] == raw['engine']['source_digest']):
                raise ValueError('Source changed or raw source digest did not match frozen run inputs.')
            metrics, outcomes = report_metrics(raw)
            metrics['subprocess.overall_ms'] = elapsed
            run.update(valid=True, engine=raw['engine'], metrics=metrics, stage_outcomes=outcomes,
                       raw_comparison_samples=raw['comparison_samples'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            run.update(valid=False, error=f'{type(error).__name__}: {error}')
        run['finished_at'] = stamp()
        save(directory / 'run.json', run)
        runs.append(run)
        print(json.dumps({'run_id': run['run_id'], 'valid': run['valid'], 'outer_warmup': run['outer_warmup'],
                          'exit_code': run.get('exit_code'), 'subprocess_overall_ms': run.get('subprocess_overall_ms'),
                          'error': run.get('error')}, ensure_ascii=False), flush=True)
    final = {'finished_at': stamp(), 'all_16_valid': len(runs) == 16 and all(run['valid'] for run in runs),
             'valid_runs': sum(run['valid'] for run in runs), 'invalid_runs': sum(not run['valid'] for run in runs),
             'scopes': SCOPES, 'runs': runs, **aggregate(runs)}
    save(out / 'comparison.json', final)
    print(json.dumps({'output': str(out), 'all_16_valid': final['all_16_valid'], 'invalid_runs': final['invalid_runs']}, ensure_ascii=False), flush=True)
    return 0 if final['all_16_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
