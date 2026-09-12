"""The host-runtime part of an input index is built once per process and shared."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hooks import click_observation_inputs as observation_inputs
from hooks import click_observer_runtime as observer_runtime

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = (
    sys.platform in {"linux", "darwin", "win32"}
    and sys.implementation.name == "cpython"
    and sys.version_info[:3] == (3, 12, 3)
)


@unittest.skipUnless(SUPPORTED, "authoritative CPython 3.12.3 native profile")
class SharedRuntimeIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = observer_runtime.prepare(ROOT)
        cls.runtime = observer_runtime.control_state(cls.build)
        cls.artifact_id = str(cls.runtime["artifact_id"])
        cls.profile = str(cls.runtime["profile"])

    def setUp(self) -> None:
        observation_inputs.clear_shared_runtime_index()
        self.addCleanup(observation_inputs.clear_shared_runtime_index)
        temporary = tempfile.TemporaryDirectory(prefix="click-shared-index-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "tests" / "test_one.py").write_text("import unittest\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)

    def snapshot(self, *, shared=None) -> observation_inputs.InputSnapshot:
        return observation_inputs.InputSnapshot(
            self.project, self.artifact_id, profile=self.profile, shared=shared
        )

    def shared(self) -> observation_inputs.InputSnapshot:
        return observation_inputs.shared_runtime_index(
            self.project, self.artifact_id, profile=self.profile
        )

    def test_the_runtime_index_is_built_once_and_excludes_repository_content(self) -> None:
        first = self.shared()
        self.assertIs(first, self.shared())
        self.assertEqual(first.project_content, {})
        project_keys = [
            key for key in first.before
            if key.startswith(observation_inputs._path_key(self.project) + "/")
        ]
        self.assertEqual(project_keys, [], project_keys[:3])
        self.assertGreater(len(first.before), 100)

    def test_a_snapshot_built_on_the_shared_index_equals_a_fresh_one(self) -> None:
        fresh = self.snapshot()
        derived = self.snapshot(shared=self.shared())
        self.assertEqual(set(derived.before), set(fresh.before))
        self.assertEqual(derived.enumerated, fresh.enumerated)
        self.assertEqual(derived.project_content, fresh.project_content)
        self.assertIn(observation_inputs._path_key(self.project / "tests" / "test_one.py"), derived.project_content)

    def test_each_snapshot_reindexes_the_repository_but_not_the_runtime(self) -> None:
        base = self.shared()
        before = self.snapshot(shared=base)
        (self.project / "tests" / "test_two.py").write_text("import unittest\n", encoding="utf-8")
        after = self.snapshot(shared=base)
        new_key = observation_inputs._path_key(self.project / "tests" / "test_two.py")
        self.assertNotIn(new_key, before.project_content)
        self.assertIn(new_key, after.project_content)
        self.assertNotIn(new_key, base.before)
        self.assertEqual(
            {key: value for key, value in after.before.items() if key in base.before},
            base.before,
        )

    def test_a_runtime_input_that_changed_after_the_shared_index_is_refused(self) -> None:
        base = self.shared()
        stdlib = Path(observation_inputs.__file__).with_name("click_observation_inputs.py")
        # Pick an indexed runtime file the interpreter reads: the stdlib's os module.
        import os as os_module
        target = Path(os_module.__file__).resolve()
        key = observation_inputs._path_key(target)
        self.assertIn(key, base.before, "the stdlib root is part of the shared index")
        base.before[key] = ["tampered"]
        snapshot = self.snapshot(shared=base)
        with self.assertRaises(observation_inputs.InputError) as raised:
            snapshot.records({str(target): {"read"}})
        self.assertEqual(str(raised.exception), "input-changed-during-execution")
        del stdlib

    def test_an_unsupported_runtime_yields_no_shared_index(self) -> None:
        self.assertIsNone(observation_inputs.shared_runtime_index(
            self.project, "not-an-artifact-id", profile=self.profile
        ))

    def test_a_replaced_snapshot_class_is_left_to_the_observer(self) -> None:
        from unittest import mock
        with mock.patch.object(observation_inputs, "InputSnapshot", side_effect=KeyboardInterrupt):
            base = observation_inputs.shared_runtime_index(self.project, self.artifact_id, profile=self.profile)
        self.assertIsNotNone(base)
        with mock.patch.object(observation_inputs, "runtime_roots", side_effect=RuntimeError("boom")):
            observation_inputs.clear_shared_runtime_index()
            self.assertIsNone(observation_inputs.shared_runtime_index(self.project, self.artifact_id, profile=self.profile))

    def test_a_shared_index_for_another_project_is_ignored(self) -> None:
        other = self.project.parent / "other"
        (other / "tests").mkdir(parents=True)
        (other / "tests" / "test_other.py").write_text("import unittest\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        foreign = observation_inputs.shared_runtime_index(other, self.artifact_id, profile=self.profile)
        derived = self.snapshot(shared=foreign)
        fresh = self.snapshot()
        self.assertEqual(set(derived.before), set(fresh.before))
        self.assertEqual(derived.project_content, fresh.project_content)


if __name__ == "__main__":
    unittest.main()
