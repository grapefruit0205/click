#!/usr/bin/env python3
"""Resume the interrupted documentation-only controlled fixture experiment."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

base = Path(__file__).parent
spec = importlib.util.spec_from_file_location('comparison_harness', base / 'compare_guarded_workflows.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
out = base / 'controlled-results'
manifest = json.loads((out / 'manifest.json').read_text())
assert not (out / 'comparison.json').exists()
assert str(Path(sys.executable).resolve()) == manifest['environment']['executable']
assert sys.version == manifest['environment']['python']
schedule = manifest['schedule']
assert len(schedule) == 16
roots = {item['version']: Path(item['cwd']) for item in schedule}
initial = manifest['initial_source_fingerprints']
for version, root in roots.items():
    assert harness.source_fingerprint(root) == initial[version], version
prefix = Path(manifest['environment']['pythonpycacheprefix'])
assert not prefix.exists()
environment = dict(os.environ)
environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', PYTHONPYCACHEPREFIX=str(prefix))
environment.pop('PYTHONPATH', None)
environment.pop('PYTHONHOME', None)
validator = harness.load_existing_validator(roots['frozen'])
runs = []
completed = []
interrupted = []
for item in schedule:
    directory = Path(item['directory'])
    if (directory / 'run.json').exists():
        run = json.loads((directory / 'run.json').read_text())
        raw = json.loads((directory / 'raw.json').read_text())
        assert run['valid'] and run['exit_code'] == 0 and validator(raw)
        assert run['bytecode_prefix_absent_before'] and run['bytecode_prefix_absent_after']
        assert run['source_digest_before'] == run['source_digest_after'] == raw['engine']['source_digest'] == initial[run['version']]['source_digest']
        completed.append(run['run_id'])
    elif directory.exists():
        preserved = out / ('interrupted-segment-1-' + item['run_id'])
        directory.rename(preserved)
        interrupted.append({'run_id': item['run_id'], 'preserved_directory': str(preserved),
                            'included_in_statistics': False, 'reason': 'Turn interruption stopped the process before a complete run sidecar was recorded.'})
harness.save(out / 'resume-segment-2.json', {
    'resumed_at': harness.stamp(), 'reason': 'Continued original work after a user status question interrupted the previous tool session.',
    'completed_runs_reused': completed, 'remaining_run_count': 16 - len(completed),
    'incomplete_attempts_preserved': interrupted,
    'conditions': manifest['environment'], 'source_fingerprints_rechecked': initial,
    'interpretation': 'Pause occurred between completed source-version pairs. Completed timings were preserved; no rerun or pooling with the excluded asymmetric-cache attempt.'})
for item in schedule:
    directory = Path(item['directory'])
    if (directory / 'run.json').exists():
        runs.append(json.loads((directory / 'run.json').read_text()))
        continue
    run = {**item, 'execution_segment': 2, 'started_at': harness.stamp()}
    directory.mkdir()
    root = roots[run['version']]
    before = harness.source_fingerprint(root)
    run['source_digest_before'] = before['source_digest']
    run['bytecode_prefix_absent_before'] = not prefix.exists()
    try:
        code, timed_out, elapsed = harness.run_cli(run['argv'], root, environment,
            directory / 'stdout.log', directory / 'stderr.log', 900.0)
        run.update(exit_code=code, timed_out=timed_out, subprocess_overall_ms=elapsed,
                   bytecode_prefix_absent_after=not prefix.exists())
        if not run['bytecode_prefix_absent_before'] or not run['bytecode_prefix_absent_after']:
            raise ValueError('Bytecode prefix unexpectedly populated.')
        after = harness.source_fingerprint(root)
        run['source_digest_after'] = after['source_digest']
        if code != 0 or timed_out:
            raise ValueError('Benchmark command failed or timed out; logs and any raw JSON preserved.')
        raw = json.loads((directory / 'raw.json').read_text())
        if not validator(raw):
            raise ValueError('Existing workflow validator rejected the raw report.')
        if (raw['conditions']['iterations'], raw['conditions']['warmups'], raw['conditions']['workload_rounds']) != (1, 0, run['rounds']):
            raise ValueError('Raw workload conditions differ from schedule.')
        if raw['environment']['python'] != platform.python_version():
            raise ValueError('Unexpected interpreter version.')
        if not before['source_digest'] == after['source_digest'] == initial[run['version']]['source_digest'] == raw['engine']['source_digest']:
            raise ValueError('Source changed or raw digest did not match.')
        metrics, outcomes = harness.report_metrics(raw)
        metrics['subprocess.overall_ms'] = elapsed
        run.update(valid=True, engine=raw['engine'], metrics=metrics, stage_outcomes=outcomes,
                   raw_comparison_samples=raw['comparison_samples'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.update(valid=False, error=f'{type(error).__name__}: {error}')
    run['finished_at'] = harness.stamp()
    harness.save(directory / 'run.json', run)
    runs.append(run)
    print(json.dumps({'run_id': run['run_id'], 'valid': run['valid'], 'exit_code': run.get('exit_code'),
                      'subprocess_overall_ms': run.get('subprocess_overall_ms'), 'error': run.get('error')}, ensure_ascii=False), flush=True)
final = {'finished_at': harness.stamp(), 'all_16_valid': len(runs) == 16 and all(run['valid'] for run in runs),
         'valid_runs': sum(run['valid'] for run in runs), 'invalid_runs': sum(not run['valid'] for run in runs),
         'execution_segments': 2, 'interrupted_incomplete_attempts_included': False,
         'scopes': harness.SCOPES, 'runs': runs, **harness.aggregate(runs)}
harness.save(out / 'comparison.json', final)
print(json.dumps({'output': str(out), 'all_16_valid': final['all_16_valid'], 'invalid_runs': final['invalid_runs']}, ensure_ascii=False), flush=True)
raise SystemExit(0 if final['all_16_valid'] else 1)
