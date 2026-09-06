from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from hooks import click_observation_cache as cache


class ClickObservationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.plugin_data = root / "plugin-data"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.plugin_data),
                "CLICK_CONFIG_HOME": str(self.plugin_data),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def request(argv: list[str], *, fresh: bool = False) -> dict[str, object]:
        value: dict[str, object] = {"version": 1, "commands": [argv]}
        if fresh:
            value["fresh"] = True
        return value

    def test_cat_result_is_content_bound_and_owner_only(self) -> None:
        target = self.workspace / "notes.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        request = self.request(["cat", "notes.txt"])
        descriptor = cache.describe(request, self.workspace)
        self.assertIsNotNone(descriptor)
        assert descriptor is not None
        key = cache.store(
            request,
            self.workspace,
            descriptor,
            b"alpha\nbeta\n",
            b"",
        )
        self.assertEqual(key, descriptor["key"])

        current, hit = cache.load(request, self.workspace)
        self.assertEqual(current, descriptor)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["stdout_data"], b"alpha\nbeta\n")
        self.assertEqual(hit["stderr_data"], b"")
        self.assertEqual(hit["source_refs"], ["notes.txt"])
        cache_root = self.plugin_data / cache.CACHE_DIR_NAME
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(cache_root.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((cache_root / f"{key}.json").stat().st_mode),
                0o600,
            )

        target.write_text("alpha\nchanged\n", encoding="utf-8")
        changed, miss = cache.load(request, self.workspace)
        self.assertIsNotNone(changed)
        self.assertNotEqual(changed["key"], key)
        self.assertIsNone(miss)

    def test_sed_range_and_rg_inventory_have_distinct_current_keys(self) -> None:
        source = self.workspace / "source.txt"
        source.write_text("one\ntwo\nthree\n", encoding="utf-8")
        first = cache.describe(
            self.request(["sed", "-n", "1,2p", "source.txt"]),
            self.workspace,
        )
        second = cache.describe(
            self.request(["sed", "-n", "2,3p", "source.txt"]),
            self.workspace,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["key"], second["key"])

        sources = self.workspace / "src"
        sources.mkdir()
        (sources / "a.py").write_text("needle = 1\n", encoding="utf-8")
        request = self.request(["rg", "-n", "needle", "src"])
        with mock.patch.dict(
            os.environ,
            {"RIPGREP_CONFIG_PATH": str(self.workspace / "missing-config")},
        ):
            original = cache.describe(request, self.workspace)
        self.assertIsNotNone(original)
        (sources / "b.py").write_text("needle = 2\n", encoding="utf-8")
        added = cache.describe(request, self.workspace)
        self.assertNotEqual(original["key"], added["key"])
        (sources / "a.py").rename(sources / "renamed.py")
        renamed = cache.describe(request, self.workspace)
        self.assertNotEqual(added["key"], renamed["key"])
        (sources / "b.py").unlink()
        deleted = cache.describe(request, self.workspace)
        self.assertNotEqual(renamed["key"], deleted["key"])
        (self.workspace / ".gitignore").write_text(
            "src/*.py\n", encoding="utf-8"
        )
        ignore_changed = cache.describe(request, self.workspace)
        self.assertNotEqual(deleted["key"], ignore_changed["key"])

    def test_fresh_bypasses_hit_and_corruption_falls_back_to_miss(self) -> None:
        (self.workspace / "notes.txt").write_text("value\n", encoding="utf-8")
        request = self.request(["cat", "notes.txt"])
        descriptor = cache.describe(request, self.workspace)
        assert descriptor is not None
        key = cache.store(
            request, self.workspace, descriptor, b"value\n", b""
        )
        fresh_descriptor, fresh_hit = cache.load(
            self.request(["cat", "notes.txt"], fresh=True), self.workspace
        )
        self.assertEqual(fresh_descriptor["key"], key)
        self.assertIsNone(fresh_hit)

        path = self.plugin_data / cache.CACHE_DIR_NAME / f"{key}.json"
        path.write_text("{broken", encoding="utf-8")
        _, corrupted = cache.load(request, self.workspace)
        self.assertIsNone(corrupted)
        self.assertFalse(path.exists())

    def test_expired_large_and_ambiguous_results_are_not_reused(self) -> None:
        (self.workspace / "notes.txt").write_text("value\n", encoding="utf-8")
        request = self.request(["cat", "notes.txt"])
        descriptor = cache.describe(request, self.workspace)
        assert descriptor is not None
        key = cache.store(
            request,
            self.workspace,
            descriptor,
            b"value\n",
            b"",
            now=10,
        )
        expired = cache.load_descriptor(
            descriptor, now=10 + cache.CACHE_TTL_SECONDS + 1
        )
        self.assertIsNone(expired)
        self.assertFalse(
            (self.plugin_data / cache.CACHE_DIR_NAME / f"{key}.json").exists()
        )
        self.assertEqual(
            cache.store(
                request,
                self.workspace,
                descriptor,
                b"x" * (cache.MAX_RESULT_BYTES + 1),
                b"",
            ),
            "",
        )
        self.assertIsNone(
            cache.describe(self.request(["rg", "--files"]), self.workspace)
        )
        self.assertIsNone(
            cache.describe(
                self.request(["rg", "-L", "needle", "."]), self.workspace
            )
        )
        self.assertIsNone(
            cache.describe(
                {"version": 1, "commands": [["cat", "notes.txt"], ["cat", "notes.txt"]]},
                self.workspace,
            )
        )
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        self.assertIsNone(
            cache.describe(self.request(["cat", str(outside)]), self.workspace)
        )

    def test_entry_digest_detects_tampering(self) -> None:
        (self.workspace / "notes.txt").write_text("value\n", encoding="utf-8")
        request = self.request(["cat", "notes.txt"])
        descriptor = cache.describe(request, self.workspace)
        assert descriptor is not None
        key = cache.store(request, self.workspace, descriptor, b"value\n", b"")
        path = self.plugin_data / cache.CACHE_DIR_NAME / f"{key}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["stdout_bytes"] += 1
        path.write_text(json.dumps(value), encoding="utf-8")
        _, hit = cache.load(request, self.workspace)
        self.assertIsNone(hit)


if __name__ == "__main__":
    unittest.main()
