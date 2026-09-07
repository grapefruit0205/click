#!/usr/bin/env python3
"""Partition the complete unittest inventory without changing Click reuse policy.

Keep each TestCase class together so class fixtures retain their normal lifetime.
Historical durations affect scheduling only; discovery is always authoritative.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMINGS = ROOT / "scripts" / "ci_test_timings.json"


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(flatten(item))
        else:
            tests.append(item)
    return tests


def partition(
    tests: list[unittest.TestCase], parts: int, durations: dict[str, float]
) -> list[list[unittest.TestCase]]:
    if parts < 1:
        raise ValueError("parts must be positive")
    identifiers = [test.id() for test in tests]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("discovery returned duplicate test ids")
    groups: dict[type, list[unittest.TestCase]] = defaultdict(list)
    for test in tests:
        groups[type(test)].append(test)
    weighted = []
    for group in groups.values():
        weight = sum(
            durations.get(test.id(), durations.get(test.id().rsplit(".", 1)[0], 1.0))
            for test in group
        )
        weighted.append((weight, group[0].id(), group))
    buckets: list[list[unittest.TestCase]] = [[] for _ in range(parts)]
    costs = [0.0] * parts
    for weight, _, group in sorted(weighted, key=lambda item: (-item[0], item[1])):
        index = min(range(parts), key=lambda i: (costs[i], len(buckets[i]), i))
        buckets[index].extend(group)
        costs[index] += weight
    order = {identifier: i for i, identifier in enumerate(identifiers)}
    for bucket in buckets:
        bucket.sort(key=lambda test: order[test.id()])
    if Counter(test.id() for bucket in buckets for test in bucket) != Counter(identifiers):
        raise ValueError("CI partitions do not cover the complete discovery inventory")
    return buckets


def read_durations(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("durations_seconds") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        raise ValueError("timings must contain durations_seconds")
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("test durations must be finite nonnegative numbers")
    return values


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations: dict[str, float] = {}
        self.started = 0.0

    def startTest(self, test):  # noqa: N802 - unittest API
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):  # noqa: N802 - unittest API
        self.durations[test.id()] = round(time.perf_counter() - self.started, 6)
        super().stopTest(test)


@contextmanager
def isolated_temp():
    """Give concurrent local partitions independent native-observer artifacts.

    The artifact cache is keyed by runtime contents under the system temp root;
    otherwise one partition's teardown can delete another's active companion.
    Children inherit the same root, just as they do on separate CI machines.
    """
    names = ("TMPDIR", "TEMP", "TMP")
    previous = {name: os.environ.get(name) for name in names}
    original_tempdir = tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix="click-ci-") as directory:
        try:
            os.environ.update({name: directory for name in names})
            tempfile.tempdir = directory
            yield
        finally:
            tempfile.tempdir = original_tempdir
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=int, default=1)
    parser.add_argument("--part", type=int, default=1, help="one-based partition")
    parser.add_argument("--timings", type=Path, default=DEFAULT_TIMINGS)
    parser.add_argument("--timings-out", type=Path)
    parser.add_argument("--list", action="store_true", help="validate and display all partitions")
    args = parser.parse_args(argv)
    if args.parts < 1 or not 1 <= args.part <= args.parts:
        parser.error("require 1 <= part <= parts")
    with isolated_temp():
        return run_partition(args, parser)


def run_partition(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    if loader.errors:
        sys.stderr.write("\n".join(loader.errors) + "\n")
        return 2
    tests = flatten(suite)
    if not tests:
        sys.stderr.write("No tests discovered.\n")
        return 2
    try:
        buckets = partition(tests, args.parts, read_durations(args.timings))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Inventory: {len(tests)} tests; partitions: {[len(b) for b in buckets]}; no missing or duplicate ids.", flush=True)
    if args.list:
        print(json.dumps([[test.id() for test in bucket] for bucket in buckets], indent=2))
        return 0
    result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(
        unittest.TestSuite(buckets[args.part - 1])
    )
    if args.timings_out:
        args.timings_out.parent.mkdir(parents=True, exist_ok=True)
        args.timings_out.write_text(json.dumps({
            "version": 1,
            "system": platform.system(),
            "python": platform.python_version(),
            "inventory_count": len(tests),
            "part": args.part,
            "parts": args.parts,
            "durations_seconds": dict(sorted(result.durations.items())),
        }, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
