from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hooks import click_verification_inputs as inputs


class VerificationInputBindingTests(unittest.TestCase):
    def test_content_absence_membership_deletion_and_rename_change_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patterns = ("config/*.json",)
            missing = inputs.snapshot(root, patterns)
            self.assertEqual(missing["status"], "complete")
            self.assertEqual(missing["match_count"], 0)

            config = root / "config"
            config.mkdir()
            first = config / "one.json"
            first.write_text('{"value":1}', encoding="utf-8")
            created = inputs.snapshot(root, patterns)
            self.assertNotEqual(created["digest"], missing["digest"])

            first.write_text('{"value":2}', encoding="utf-8")
            changed = inputs.snapshot(root, patterns)
            self.assertNotEqual(changed["digest"], created["digest"])

            second = config / "two.json"
            first.rename(second)
            renamed = inputs.snapshot(root, patterns)
            self.assertNotEqual(renamed["digest"], changed["digest"])

            second.unlink()
            deleted = inputs.snapshot(root, patterns)
            self.assertEqual(deleted["digest"], missing["digest"])

    def test_paths_are_bounded_and_symlinks_never_authorize_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.txt").write_text("one", encoding="utf-8")
            (root / "two.txt").write_text("two", encoding="utf-8")
            with mock.patch.object(inputs, "MAX_MATCHED_FILES", 1):
                limited = inputs.snapshot(root, ("*.txt",))
            self.assertEqual(limited["status"], "unavailable")
            self.assertEqual(limited["reason"], "explicit-input-match-limit")

            link = root / "linked.txt"
            try:
                link.symlink_to(root / "one.txt")
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable on this host")
            linked = inputs.snapshot(root, ("linked.txt",))
            self.assertEqual(linked["status"], "unavailable")
            self.assertEqual(linked["reason"], "explicit-input-symlink")

    def test_sensitive_inputs_are_rejected_without_exporting_paths_or_contents(self) -> None:
        normalized, reason = inputs.normalize_patterns([".env"])
        self.assertIsNone(normalized)
        self.assertEqual(reason, "explicit-input-sensitive-path")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = "CLICK_PRIVATE_VALUE_47"
            (root / ".env").write_text(secret, encoding="utf-8")
            binding = inputs.snapshot(root, ("**",))
            rendered = json.dumps(binding, sort_keys=True)
            self.assertEqual(binding["status"], "unavailable")
            self.assertEqual(binding["reason"], "explicit-input-sensitive-match")
            self.assertNotIn(".env", rendered)
            self.assertNotIn(secret, rendered)

    def test_pattern_boundaries_reject_external_and_traversal_paths(self) -> None:
        for pattern in ("/tmp/input.txt", "../input.txt", r"C:\\input.txt"):
            with self.subTest(pattern=pattern):
                normalized, reason = inputs.normalize_patterns([pattern])
                self.assertIsNone(normalized)
                self.assertEqual(reason, "explicit-input-pattern-invalid")


if __name__ == "__main__":
    unittest.main()
