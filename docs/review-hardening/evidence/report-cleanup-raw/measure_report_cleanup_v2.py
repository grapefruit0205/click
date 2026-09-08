#!/usr/bin/env python3
"""Isolated report cleanup component benchmark; no source writes.
Timings and tracemalloc runs are separate. Operation instrumentation is separate.
"""
from __future__ import annotations
import argparse, contextlib, hashlib, io, json, os, platform, statistics, sys, tempfile, time, tracemalloc
from pathlib import Path
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--repetitions', type=int, default=11)
args = parser.parse_args()
source = Path(args.source).resolve()
sys.path.insert(0, str(source))
from hooks import click_gate

PREFIX = click_gate.JSON_REPORT_FILE_PREFIX
REPORT = {'payload': '검증 상태: report cleanup component baseline'}
original_renderer = click_gate.click_runner_transport.render_runner_shell_command
original_scandir, original_stat, original_unlink, original_glob = os.scandir, Path.stat, Path.unlink, Path.glob

class CountingEntry:
    def __init__(self, entry, counters):
        self.entry, self.counters = entry, counters
    def __getattr__(self, name): return getattr(self.entry, name)
    def stat(self, *args, **kwargs):
        self.counters['direntry_stat_calls'] += 1
        if kwargs.get('follow_symlinks') is False:
            self.counters['nofollow_direntry_stat_calls'] += 1
        return self.entry.stat(*args, **kwargs)

class CountingScandir:
    def __init__(self, path, counters):
        self.inner = original_scandir(path)
        self.counters = counters
    def __enter__(self):
        self.inner.__enter__()
        return self
    def __exit__(self, *exc):
        return self.inner.__exit__(*exc)
    def __iter__(self): return self
    def __next__(self):
        value = next(self.inner)
        self.counters['directory_entries_yielded'] += 1
        return CountingEntry(value, self.counters)


def stats(values):
    return {'samples': values, 'median': statistics.median(values), 'min': min(values), 'max': max(values)}


def run_case(matches, nonmatches):
    with tempfile.TemporaryDirectory(prefix='click-report-cleanup-') as temporary:
        root = Path(temporary) / 'gate-state'
        root.mkdir()
        # Matching files are created first; do not assume filesystem iteration order.
        for index in range(matches):
            (root / f'{PREFIX}{index:032x}.json').write_text('{}')
        for index in range(nonmatches):
            (root / f'contract-{index:032x}.json').write_text('{}')
        existing_names = {f"{PREFIX}{index:032x}.json" for index in range(matches)}
        rendered_paths = []
        def fallback_renderer(argv):
            if argv[1] == '-c':
                return 'exit 2'
            rendered_paths.append(Path(argv[-1]))
            return original_renderer(argv)
        def call(mode):
            if mode == 'fallback':
                with mock.patch.object(click_gate.click_runner_transport, 'render_runner_shell_command', fallback_renderer):
                    return click_gate._json_report_command(REPORT)
            return click_gate._json_report_command(REPORT)
        def remove_new():
            for path in rendered_paths:
                path.unlink(missing_ok=True)
            rendered_paths.clear()
        result = {'existing_matching_files': matches, 'nonmatching_files': nonmatches, 'file_age': 'fresh (< TTL)'}
        with mock.patch.dict(os.environ, {'PLUGIN_DATA': temporary}):
            for mode in ('inline', 'fallback'):
                for _ in range(2):
                    call(mode)
                    remove_new()
                times = []
                for _ in range(args.repetitions):
                    start = time.perf_counter_ns()
                    call(mode)
                    times.append((time.perf_counter_ns() - start) / 1_000_000)
                    remove_new()
                peaks = []
                for _ in range(3):
                    tracemalloc.start()
                    tracemalloc.reset_peak()
                    call(mode)
                    _, peak = tracemalloc.get_traced_memory()
                    tracemalloc.stop()
                    peaks.append(peak)
                    remove_new()
                counters = {'directory_entries_yielded': 0, 'matching_paths_yielded': 0, 'matching_stat_calls': 0, 'other_stat_calls': 0, 'unlink_calls': 0, 'direntry_stat_calls': 0, 'nofollow_direntry_stat_calls': 0, 'existing_report_path_stat_calls': 0}
                def scandir(path):
                    return CountingScandir(path, counters)
                def pathstat(path, *pargs, **kwargs):
                    if path.parent == root and path.name in existing_names:
                        counters['existing_report_path_stat_calls'] += 1
                    counters['matching_stat_calls' if path.parent == root and path.name.startswith(PREFIX) else 'other_stat_calls'] += 1
                    return original_stat(path, *pargs, **kwargs)
                def unlink(path, *pargs, **kwargs):
                    counters['unlink_calls'] += 1
                    return original_unlink(path, *pargs, **kwargs)
                def glob(path, pattern):
                    for found in original_glob(path, pattern):
                        counters['matching_paths_yielded'] += 1
                        yield found
                with mock.patch.object(os, 'scandir', scandir), mock.patch.object(Path, 'stat', pathstat), mock.patch.object(Path, 'unlink', unlink), mock.patch.object(Path, 'glob', glob):
                    call(mode)
                result[mode] = {'wall_ms': stats(times), 'peak_python_bytes': stats(peaks), 'operations': counters}
                remove_new()
        return result


def starvation():
    from contextlib import contextmanager
    with tempfile.TemporaryDirectory(prefix='click-report-starvation-') as temporary:
        root = Path(temporary) / 'gate-state'
        root.mkdir()
        fresh = [root / f'{PREFIX}{i:032x}.json' for i in range(128)]
        stale = [root / f'{PREFIX}{i:032x}.json' for i in range(128, 136)]
        for path in fresh + stale:
            path.write_text('{}')
        expired = time.time() - click_gate.JSON_REPORT_MAX_AGE_SECONDS - 10
        for path in stale:
            os.utime(path, (expired, expired))
        report_paths = []
        deletions = []
        visited_counts = []
        @contextmanager
        def ordered_scandir(path):
            with original_scandir(path) as entries:
                by_name = {entry.name: entry for entry in entries}
            names = [p.name for p in fresh + stale if p.name in by_name]
            names.extend(name for name in by_name if name not in names)
            def iterate():
                for name in names:
                    visited_counts[-1] += 1
                    yield by_name[name]
            yield iterate()
        def fallback_renderer(argv):
            if argv[1] == '-c': return 'exit 2'
            report_paths.append(Path(argv[-1]))
            return original_renderer(argv)
        def unlink(path, *args, **kwargs):
            result = original_unlink(path, *args, **kwargs)
            deletions[-1] += 1
            return result
        remaining = []
        with mock.patch.dict(os.environ, {'PLUGIN_DATA': temporary}), mock.patch.object(os, 'scandir', ordered_scandir), mock.patch.object(Path, 'unlink', unlink), mock.patch.object(click_gate.click_runner_transport, 'render_runner_shell_command', fallback_renderer):
            for _ in range(6):
                deletions.append(0)
                visited_counts.append(0)
                click_gate._json_report_command(REPORT)
                remaining.append(sum(p.exists() for p in stale))
        return {'mechanism': 'same actual tempfile files/stat/unlink for both versions with deterministic scandir: 128 fresh then 8 stale; valid writer names; renderer forces fallback', 'repeated_fallback_calls': 6, 'stale_remaining_after_each_call': remaining, 'deletions_per_call': deletions, 'directory_entries_per_call': visited_counts, 'fresh_preserved': all(p.exists() for p in fresh), 'new_reports_preserved': all(p.exists() for p in report_paths)}

output = {
    'environment': {'python': sys.version, 'platform': platform.platform(), 'source': str(source), 'gate_sha256': hashlib.sha256((source / 'hooks/click_gate.py').read_bytes()).hexdigest(), 'pid': os.getpid()},
    'method': {'harness_version': 2, 'warmup_per_case_mode': 2, 'timing_repetitions': args.repetitions, 'memory_repetitions': 3, 'payload': REPORT, 'fallback': 'renderer returns exit 2 only for inline -c argv, actual fallback write/cleanup/runner rendering retained', 'inline': 'actual POSIX renderer; no directory cleanup', 'scope': 'warm in-process _json_report_command component only; fixture population and post-call deletion excluded; timings include lightweight fallback mock context; tracemalloc captures Python allocation not total RSS; concurrent unrelated tests may introduce noise; no whole-task or token metric'},
    'cases': [run_case(0,0), run_case(16,0), run_case(6000,0), run_case(16,6000), run_case(6000,6000)],
    'starvation': starvation(),
}
Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'output': args.output, 'summary': [{'matching': c['existing_matching_files'], 'nonmatching': c['nonmatching_files'], 'inline_median_ms': c['inline']['wall_ms']['median'], 'fallback_median_ms': c['fallback']['wall_ms']['median'], 'fallback_peak_median_bytes': c['fallback']['peak_python_bytes']['median'], 'operations': c['fallback']['operations']} for c in output['cases']], 'starvation': output['starvation']}, ensure_ascii=False, indent=2))
