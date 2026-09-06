from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryShardInventoryTests(unittest.TestCase):
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
