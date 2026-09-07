from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.run_ci_tests import isolated_temp, partition


ROOT = Path(__file__).resolve().parents[1]


class RepositoryShardInventoryTests(unittest.TestCase):
    def test_ci_partition_temp_root_is_isolated_and_inherited_by_children(self) -> None:
        previous = tempfile.gettempdir()
        with isolated_temp():
            root = tempfile.gettempdir()
            self.assertNotEqual(root, previous)
            child = subprocess.run(
                [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(child.stdout.strip(), root)
        self.assertEqual(tempfile.gettempdir(), previous)
        self.assertFalse(Path(root).exists())

    def test_ci_partition_covers_every_test_once_and_keeps_class_fixtures_together(self) -> None:
        class Slow(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        class Fast(unittest.TestCase):
            def test_c(self):
                pass

        tests = [Slow("test_a"), Slow("test_b"), Fast("test_c")]
        durations = {test.id(): 5.0 if isinstance(test, Slow) else 1.0 for test in tests}
        buckets = partition(tests, 2, durations)
        self.assertEqual(sorted(test.id() for bucket in buckets for test in bucket), sorted(test.id() for test in tests))
        self.assertEqual(len({i for i, bucket in enumerate(buckets) for test in bucket if isinstance(test, Slow)}), 1)
        self.assertEqual(sorted(len(bucket) for bucket in buckets), [1, 2])
        self.assertEqual(buckets, partition(tests, 2, durations))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            partition([tests[0], tests[0]], 2, durations)

    def test_every_parent_discovery_file_has_one_shard_owner(self) -> None:
        policy = json.loads(
            (ROOT / ".click/evidence-shards.json").read_text(encoding="utf-8")
        )
        self.assertEqual(policy.get("version"), 1)
        entries = policy.get("entries")
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        patterns = entry["inventory"]
        discovered = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "tests").rglob("*.py")
            if any(
                fnmatch.fnmatchcase(path.relative_to(ROOT).as_posix(), pattern)
                for pattern in patterns
            )
        )
        covered = [
            path
            for shard in entry["shards"]
            for path in shard["covers"]
        ]
        self.assertEqual(sorted(covered), discovered)
        self.assertEqual(len(covered), len(set(covered)))


if __name__ == "__main__":
    unittest.main()
