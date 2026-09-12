from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hooks import click_dependency_cache


SHADOW_FIXTURES = Path(__file__).parent / "fixtures" / "shadow-observer-v1"


class ClickDependencyCacheFixture:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "unit.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.md").write_text("a\n", encoding="utf-8")
        (self.root / "docs" / "b.md").write_text("b\n", encoding="utf-8")
        self.checks = {
            "source": [
                {
                    "evidence_id": "E1",
                    "argv": ["python3", "-m", "pytest", "tests/unit"],
                    "class": "targeted",
                }
            ]
        }

    def git_capture(self, cwd: Path, arguments: list[str]) -> bytes | None:
        completed = subprocess.run(
            [
                "git",
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        return completed.stdout if completed.returncode == 0 else None

    def commit(self, message: str = "fixture") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Click Tests",
                "-c",
                "user.email=click-tests@example.invalid",
                "commit",
                "--quiet",
                "-m",
                message,
            ],
            cwd=self.root,
            check=True,
        )

    def write_manifest(self, entries: list[dict[str, object]]) -> None:
        destination = self.root / ".click" / "evidence-dependencies.json"
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(
            json.dumps({"version": 1, "entries": entries}, indent=2) + "\n",
            encoding="utf-8",
        )

    def entry(
        self,
        paths: list[str],
        argv: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "checks": [argv or self.checks["source"][0]["argv"]],
            "paths": paths,
        }

    def receipts(
        self,
        declarations: dict[str, list[str]] | None = None,
        observations: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, dict[str, object]]:
        return click_dependency_cache.receipts_for_groups(
            self.root,
            self.checks,
            declarations=declarations,
            observations=observations,
            git_capture=self.git_capture,
        )


class ClickDependencyCacheTests(ClickDependencyCacheFixture, unittest.TestCase):
    def test_module_has_no_upward_runtime_dependency(self) -> None:
        source = Path(click_dependency_cache.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for forbidden in (
            "click_gate",
            "click_contract",
            "click_evidence",
            "click_state",
            "click_process",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in imported),
                    imported,
                )

    def test_approved_contract_patterns_track_only_the_resolved_set(self) -> None:
        self.commit()
        first = self.receipts({"source": ["src/*.py"]})["source"]
        (self.root / "docs" / "a.md").write_text("unrelated\n", encoding="utf-8")
        unrelated = self.receipts({"source": ["src/*.py"]})["source"]
        self.assertEqual(first["entry_digest"], unrelated["entry_digest"])
        self.assertEqual(first["dependency_digest"], unrelated["dependency_digest"])
        self.assertEqual(first["resolved_paths"], ["src/unit.py"])

        (self.root / "src" / "unit.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = self.receipts({"source": ["src/*.py"]})["source"]
        self.assertNotEqual(first["dependency_digest"], changed["dependency_digest"])


class ShadowObserverContractTests(unittest.TestCase):
    def fixture(self, name: str) -> dict[str, object]:
        return json.loads(
            (SHADOW_FIXTURES / name).read_text(encoding="utf-8")
        )

    def clone(self, value: dict[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(value))

    def test_canonical_fixtures_are_valid_and_digestible(self) -> None:
        for name in ("complete.json", "partial.json", "unavailable.json"):
            with self.subTest(name=name):
                record = self.fixture(name)
                normalized, error = (
                    click_dependency_cache.normalize_shadow_observer_record(
                        record
                    )
                )
                self.assertEqual(error, "")
                self.assertEqual(normalized, record)
                self.assertTrue(
                    click_dependency_cache.shadow_observer_record_is_valid(
                        record
                    )
                )
                self.assertRegex(
                    click_dependency_cache.shadow_observer_record_digest(
                        record
                    ),
                    r"^[0-9a-f]{64}$",
                )

    def test_builder_merges_and_sorts_inputs_deterministically(self) -> None:
        inputs = [
            {
                "path": "src/token.py",
                "kind": "file",
                "operations": ["read"],
            },
            {
                "path": "docs/",
                "kind": "directory",
                "operations": ["enumerate"],
            },
            {
                "path": "src/token.py",
                "kind": "file",
                "operations": ["metadata", "read"],
            },
        ]
        parameters = {
            "evidence_key": "a" * 64,
            "check_digest": "b" * 64,
            "mutation_revision": 9,
            "backend_name": "strace",
            "backend_version": "6.8",
            "backend_digest": "c" * 64,
            "command_duration_ms": 100,
            "observer_overhead_ms": 2,
        }

        first = click_dependency_cache.shadow_observer_record(
            inputs=inputs,
            **parameters,
        )
        second = click_dependency_cache.shadow_observer_record(
            inputs=reversed(inputs),
            **parameters,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first["inputs"],
            [
                {
                    "path": "docs/",
                    "kind": "directory",
                    "operations": ["enumerate"],
                },
                {
                    "path": "src/token.py",
                    "kind": "file",
                    "operations": ["metadata", "read"],
                },
            ],
        )
        self.assertEqual(first["ineligibility_reasons"], ["shadow-mode"])
        self.assertIs(first["authoritative"], False)
        self.assertIs(first["reuse_authorized"], False)

    def test_partial_record_derives_every_fail_closed_reason(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key="d" * 64,
            check_digest="e" * 64,
            mutation_revision=10,
            backend_name="strace",
            backend_version="6.8",
            backend_digest="f" * 64,
            status="partial",
            external_input_count=1,
            unresolved_event_count=2,
            child_process_count=1,
            process_tree_complete=False,
            command_duration_ms=200,
            observer_overhead_ms=3,
        )

        self.assertEqual(
            record["ineligibility_reasons"],
            [
                "external-input",
                "observation-partial",
                "process-tree-incomplete",
                "shadow-mode",
                "unresolved-event",
            ],
        )

    def test_external_inputs_are_aggregate_only(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key="1" * 64,
            check_digest="2" * 64,
            mutation_revision=11,
            backend_name="strace",
            backend_version="6.8",
            backend_digest="3" * 64,
            external_input_count=2,
            command_duration_ms=20,
            observer_overhead_ms=1,
        )
        encoded = json.dumps(record, sort_keys=True)

        self.assertEqual(record["external_input_count"], 2)
        self.assertIn("external-input", record["ineligibility_reasons"])
        self.assertNotIn("/home/", encoded)
        self.assertNotIn("environment", encoded)
        self.assertNotIn("content", encoded)

    def test_unknown_private_or_noncanonical_data_fails_strict_validation(self) -> None:
        base = self.fixture("complete.json")
        authoritative = self.clone(base)
        authoritative["authoritative"] = True
        reuse = self.clone(base)
        reuse["reuse_authorized"] = True
        private = self.clone(base)
        private["environment"] = {"TOKEN": "secret"}
        absolute = self.clone(base)
        absolute["inputs"][0]["path"] = "/home/user/private"  # type: ignore[index]
        traversal = self.clone(base)
        traversal["inputs"][0]["path"] = "../private"  # type: ignore[index]
        noncanonical = self.clone(base)
        noncanonical["inputs"][0]["path"] = "src//token.py"  # type: ignore[index]
        missing_reason = self.clone(base)
        missing_reason["ineligibility_reasons"] = []
        unknown_version = self.clone(base)
        unknown_version["version"] = 2

        for name, record in (
            ("authoritative", authoritative),
            ("reuse", reuse),
            ("private", private),
            ("absolute", absolute),
            ("traversal", traversal),
            ("noncanonical", noncanonical),
            ("missing-reason", missing_reason),
            ("unknown-version", unknown_version),
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    click_dependency_cache.shadow_observer_record_is_valid(
                        record
                    )
                )
                self.assertEqual(
                    click_dependency_cache.shadow_observer_record_digest(record),
                    "",
                )

    def test_malformed_types_and_contradictory_statuses_fail_closed(self) -> None:
        base = self.fixture("complete.json")
        bad_status = self.clone(base)
        bad_status["status"] = []
        bad_kind = self.clone(base)
        bad_kind["inputs"][0]["kind"] = {}  # type: ignore[index]
        bad_operation = self.clone(base)
        bad_operation["inputs"][0]["operations"] = [{}]  # type: ignore[index]
        bad_counter = self.clone(base)
        bad_counter["child_process_count"] = True
        excessive_overhead = self.clone(base)
        excessive_overhead["observer_overhead_ms"] = 20_000
        incomplete = self.clone(base)
        incomplete["process_tree_complete"] = False
        partial = self.clone(base)
        partial["status"] = "partial"
        failed = self.clone(base)
        failed["status"] = "failed"
        unavailable = self.fixture("unavailable.json")
        unavailable["inputs"] = [
            {"path": "src/token.py", "kind": "file", "operations": ["read"]}
        ]

        for name, record in (
            ("bad-status", bad_status),
            ("bad-kind", bad_kind),
            ("bad-operation", bad_operation),
            ("bad-counter", bad_counter),
            ("excessive-overhead", excessive_overhead),
            ("incomplete-complete", incomplete),
            ("complete-partial", partial),
            ("complete-failed", failed),
            ("events-unavailable", unavailable),
        ):
            with self.subTest(name=name):
                normalized, error = (
                    click_dependency_cache.normalize_shadow_observer_record(
                        record
                    )
                )
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_input_and_serialized_size_limits_are_enforced(self) -> None:
        base = self.fixture("complete.json")
        too_many = self.clone(base)
        too_many["inputs"] = [
            {"path": f"src/{index}.py", "kind": "file", "operations": ["read"]}
            for index in range(
                click_dependency_cache.MAX_SHADOW_OBSERVER_INPUTS + 1
            )
        ]
        normalized, error = (
            click_dependency_cache.normalize_shadow_observer_record(too_many)
        )
        self.assertIsNone(normalized)
        self.assertIn("entry limit", error)

        large_inputs = [
            {
                "path": f"generated/{index:04d}-{'x' * 96}.json",
                "kind": "file",
                "operations": ["metadata", "read"],
            }
            for index in range(2_500)
        ]
        with self.assertRaisesRegex(ValueError, "serialized byte limit"):
            click_dependency_cache.shadow_observer_record(
                evidence_key="4" * 64,
                check_digest="5" * 64,
                mutation_revision=12,
                backend_name="strace",
                backend_version="6.8",
                backend_digest="6" * 64,
                inputs=large_inputs,
                command_duration_ms=100,
                observer_overhead_ms=1,
            )

    def test_legacy_runtime_observation_shape_is_unchanged(self) -> None:
        legacy = click_dependency_cache.dependency_observation(
            ["src/token.py"],
            external_access=True,
            child_processes=2,
        )

        self.assertEqual(set(legacy), click_dependency_cache.OBSERVATION_FIELDS)
        self.assertEqual(
            legacy["provider"],
            click_dependency_cache.OBSERVATION_PROVIDER_NAME,
        )
        self.assertTrue(
            click_dependency_cache.dependency_observation_is_valid(legacy)
        )
        self.assertFalse(
            click_dependency_cache.shadow_observer_record_is_valid(legacy)
        )


class ClickDependencyBehaviorTests(
    ClickDependencyCacheFixture,
    unittest.TestCase,
):
    def test_double_star_is_cross_directory_and_star_is_one_segment(self) -> None:
        nested = self.root / "src" / "nested"
        nested.mkdir()
        (nested / "more.py").write_text("MORE = 1\n", encoding="utf-8")
        self.commit()

        shallow = self.receipts({"source": ["src/*.py"]})["source"]
        recursive = self.receipts({"source": ["src/**/*.py"]})["source"]
        self.assertEqual(shallow["resolved_paths"], ["src/unit.py"])
        self.assertEqual(
            recursive["resolved_paths"],
            ["src/nested/more.py", "src/unit.py"],
        )

    def test_unrelated_manifest_entry_change_keeps_relevant_entry_current(self) -> None:
        self.write_manifest(
            [
                self.entry(["src/"]),
                self.entry(["docs/a.md"], ["python3", "-m", "pytest", "docs"]),
            ]
        )
        self.commit("initial manifest")
        first = self.receipts()["source"]

        self.write_manifest(
            [
                self.entry(["src/"]),
                self.entry(["docs/b.md"], ["python3", "-m", "pytest", "docs"]),
            ]
        )
        self.commit("unrelated entry")
        second = self.receipts()["source"]

        self.assertNotEqual(first["manifest_digest"], second["manifest_digest"])
        self.assertEqual(first["entry_digest"], second["entry_digest"])
        self.assertEqual(first["dependency_digest"], second["dependency_digest"])

    def test_committed_manifest_remains_authority_until_a_new_commit(self) -> None:
        self.write_manifest([self.entry(["src/unit.py"])])
        self.commit()
        first = self.receipts()["source"]

        self.write_manifest([self.entry(["src/", "docs/a.md"])])
        uncommitted = self.receipts()["source"]
        self.assertEqual(first["entry_digest"], uncommitted["entry_digest"])
        self.assertEqual(first["dependency_digest"], uncommitted["dependency_digest"])

        manifest = self.root / ".click" / "evidence-dependencies.json"
        manifest.write_text("{not-json\n", encoding="utf-8")
        malformed = self.receipts()["source"]
        self.assertEqual(first["entry_digest"], malformed["entry_digest"])
        self.assertEqual(first["dependency_digest"], malformed["dependency_digest"])

        self.write_manifest([self.entry(["src/", "docs/a.md"])])
        self.commit("relevant entry")
        second = self.receipts()["source"]
        self.assertNotEqual(first["entry_digest"], second["entry_digest"])
        self.assertNotEqual(first["dependency_digest"], second["dependency_digest"])

    def test_complete_observation_refines_expanding_manifest_patterns(self) -> None:
        self.write_manifest([self.entry(["**", "docs/a.md"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(
            ["src/unit.py"]
        )

        first = self.receipts(observations={"source": observation})["source"]
        self.assertEqual(first["resolved_paths"], ["docs/a.md", "src/unit.py"])

        (self.root / "docs" / "b.md").write_text(
            "unrelated\n", encoding="utf-8"
        )
        unrelated = self.receipts(observations={"source": observation})["source"]
        self.assertEqual(first["dependency_digest"], unrelated["dependency_digest"])

        (self.root / "docs" / "a.md").write_text("required\n", encoding="utf-8")
        required = self.receipts(observations={"source": observation})["source"]
        self.assertNotEqual(first["dependency_digest"], required["dependency_digest"])

    def test_incomplete_observation_cannot_refine_expanding_patterns(self) -> None:
        self.write_manifest([self.entry(["**"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(
            ["src/unit.py"], status="failed", process_tree_complete=False
        )

        receipt = self.receipts(observations={"source": observation})["source"]

        self.assertIn("docs/b.md", receipt["resolved_paths"])
        self.assertFalse(
            click_dependency_cache.dependency_observation_is_complete(
                receipt["observation"]
            )
        )

    def test_worktree_manifest_change_invalidates_when_the_check_reads_it(self) -> None:
        self.write_manifest([self.entry(["**"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(
            [click_dependency_cache.CONFIG_RELATIVE_PATH, "src/unit.py"]
        )
        first = self.receipts(observations={"source": observation})["source"]

        manifest = self.root / ".click" / "evidence-dependencies.json"
        manifest.write_text("{not-json\n", encoding="utf-8")
        changed = self.receipts(observations={"source": observation})["source"]

        self.assertEqual(first["entry_digest"], changed["entry_digest"])
        self.assertNotEqual(first["dependency_digest"], changed["dependency_digest"])

    def test_committed_manifest_accepts_windows_checkout_line_endings(self) -> None:
        subprocess.run(
            ["git", "config", "core.autocrlf", "true"],
            cwd=self.root,
            check=True,
        )
        self.write_manifest([self.entry(["src/unit.py"])])
        manifest = self.root / ".click" / "evidence-dependencies.json"
        manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
        self.commit("windows checkout manifest")

        self.assertIn(b"\r\n", manifest.read_bytes())
        receipt = self.receipts()["source"]
        self.assertEqual(receipt["resolved_paths"], ["src/unit.py"])

        manifest.write_bytes(
            manifest.read_bytes().replace(b"src/unit.py", b"docs/a.md")
        )
        uncommitted = self.receipts()["source"]
        self.assertEqual(receipt["entry_digest"], uncommitted["entry_digest"])
        self.commit("windows checkout manifest update")
        committed_update = self.receipts()["source"]
        self.assertEqual(committed_update["resolved_paths"], ["docs/a.md"])
        self.assertNotEqual(receipt["entry_digest"], committed_update["entry_digest"])

    @unittest.skipIf(os.name == "nt", "Windows symlink creation needs host privileges")
    def test_internal_symlink_hashes_link_and_target_but_external_is_rejected(self) -> None:
        (self.root / "linked.py").symlink_to("src/unit.py")
        self.commit()
        first = self.receipts({"source": ["linked.py"]})["source"]
        self.assertEqual(first["resolved_paths"], ["linked.py", "src/unit.py"])

        (self.root / "src" / "unit.py").write_text("VALUE = 3\n", encoding="utf-8")
        changed = self.receipts({"source": ["linked.py"]})["source"]
        self.assertNotEqual(first["dependency_digest"], changed["dependency_digest"])

        (self.root / "linked.py").unlink()
        (self.root / "linked.py").symlink_to(Path(self.temporary.name) / "outside.py")
        (Path(self.temporary.name) / "outside.py").write_text("outside\n", encoding="utf-8")
        self.assertEqual(self.receipts({"source": ["linked.py"]}), {})

    def test_ambiguous_patterns_are_rejected(self) -> None:
        for pattern in (
            "../src/",
            "/src/",
            "src/**x.py",
            "src/file?.py",
            "src/[ab].py",
            "src\\unit.py",
        ):
            with self.subTest(pattern=pattern):
                normalized, error = click_dependency_cache.normalize_patterns(
                    [pattern]
                )
                self.assertIsNone(normalized)
                self.assertTrue(error)

    def test_observed_dependency_fills_a_silent_manifest_gap(self) -> None:
        (self.root / "src" / "shared.py").write_text(
            "SHARED = 1\n", encoding="utf-8"
        )
        self.write_manifest([self.entry(["src/unit.py"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(
            ["src/shared.py"]
        )

        first = self.receipts(observations={"source": observation})["source"]

        self.assertEqual(
            first["resolved_paths"], ["src/shared.py", "src/unit.py"]
        )
        self.assertTrue(
            click_dependency_cache.dependency_observation_is_complete(
                first["observation"]
            )
        )
        (self.root / "src" / "shared.py").write_text(
            "SHARED = 2\n", encoding="utf-8"
        )
        changed = self.receipts(observations={"source": observation})["source"]
        self.assertNotEqual(first["dependency_digest"], changed["dependency_digest"])

    def test_failed_or_unavailable_observation_is_explicitly_incomplete(self) -> None:
        self.write_manifest([self.entry(["src/unit.py"])])
        self.commit()

        unavailable = self.receipts()["source"]["observation"]
        failed = click_dependency_cache.dependency_observation(
            ["src/unit.py"], status="failed", process_tree_complete=False
        )

        self.assertEqual(unavailable["status"], "unavailable")
        self.assertFalse(
            click_dependency_cache.dependency_observation_is_complete(unavailable)
        )
        self.assertFalse(
            click_dependency_cache.dependency_observation_is_complete(failed)
        )

    def test_external_input_marks_a_successful_trace_incomplete(self) -> None:
        observation = click_dependency_cache.dependency_observation(
            ["src/unit.py"], external_access=True
        )

        self.assertTrue(
            click_dependency_cache.dependency_observation_is_valid(observation)
        )
        self.assertFalse(
            click_dependency_cache.dependency_observation_is_complete(observation)
        )

    def test_bytecode_cache_identity_ignores_the_source_validation_header(self) -> None:
        from hooks import click_observation_inputs as observation_inputs

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "__pycache__"
            cache.mkdir()
            pyc = cache / "module.cpython-312.pyc"
            magic, flags = b"\x6f\x0d\x0d\x0a", b"\x00\x00\x00\x00"
            code = b"marshalled code object bytes"
            reader = object.__new__(observation_inputs.InputSnapshot)
            reader.total_bytes = 0

            def fingerprint(validation: bytes, body: bytes = code, target: Path = pyc) -> str:
                target.write_bytes(magic + flags + validation + body)
                return reader._fingerprint(target, {"read"})[0]

            resaved = fingerprint(b"\x01\x00\x00\x00\x10\x00\x00\x00")
            self.assertEqual(resaved, fingerprint(b"\x09\x00\x00\x00\x10\x00\x00\x00"))
            self.assertNotEqual(resaved, fingerprint(b"\x01\x00\x00\x00\x10\x00\x00\x00", body=code + b"!"))
            self.assertNotEqual(resaved, fingerprint(b"\x01\x00\x00\x00\x10\x00\x00\x00", body=code[:-1]))
            # Only bytecode caches get the special case; a same-named file
            # outside __pycache__ and a truncated header bind their full content.
            plain = Path(directory) / "module.cpython-312.pyc"
            self.assertNotEqual(
                fingerprint(b"\x01\x00\x00\x00\x10\x00\x00\x00", target=plain),
                fingerprint(b"\x09\x00\x00\x00\x10\x00\x00\x00", target=plain),
            )
            short = cache / "short.cpython-312.pyc"
            short.write_bytes(magic + flags + b"\x01\x00")
            first = reader._fingerprint(short, {"read"})[0]
            short.write_bytes(magic + flags + b"\x02\x00")
            self.assertNotEqual(first, reader._fingerprint(short, {"read"})[0])

    def test_directory_membership_ignores_a_bytecode_cache_directory(self) -> None:
        from hooks import click_observation_inputs as observation_inputs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            reader = object.__new__(observation_inputs.InputSnapshot)
            reader.total_bytes = 0
            before = reader._fingerprint(root, {"metadata"})
            # The interpreter creating its cache beside the sources is not a
            # membership change; any other new sibling still is.
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"cache")
            self.assertEqual(before, reader._fingerprint(root, {"metadata"}))
            (root / "sibling.py").write_text("", encoding="utf-8")
            self.assertNotEqual(before, reader._fingerprint(root, {"metadata"}))
    def test_one_identity_pass_reads_each_shared_input_once(self) -> None:
        from hooks import click_observation_inputs as observation_inputs

        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared.txt"
            shared.write_text("first", encoding="utf-8")
            reads = []
            original = observation_inputs.InputSnapshot._fingerprint

            def counted(self, path, operations, *, warm=False):
                reads.append(str(path))
                return original(self, path, operations, warm=warm)

            with mock.patch.object(observation_inputs.InputSnapshot, "_fingerprint", counted):
                snapshot = object.__new__(observation_inputs.InputSnapshot)
                snapshot.total_bytes = 0
                digest = snapshot._fingerprint(shared, {"read"})[0]
                reads.clear()
                records = [{"root": "project", "path": "shared.txt", "kind": "file",
                            "operations": ["read"], "digest": digest}]
                roots = {"project": Path(directory)}
                with mock.patch.object(observation_inputs, "runtime_roots", return_value=roots):
                    # Without a pass each source reads the file again.
                    for _ in range(3):
                        self.assertTrue(observation_inputs.records_current(
                            Path(directory), "click-native-observer-" + "0" * 32, records))
                    self.assertEqual(len(reads), 3)
                    reads.clear()
                    # One decision reads it once, however many sources share it.
                    with observation_inputs.identity_pass():
                        for _ in range(3):
                            self.assertTrue(observation_inputs.records_current(
                                Path(directory), "click-native-observer-" + "0" * 32, records))
                        self.assertEqual(len(reads), 1)
                        # The decision is one instant: a write during it does not
                        # split the sources into disagreeing answers.
                        shared.write_text("second", encoding="utf-8")
                        self.assertTrue(observation_inputs.records_current(
                            Path(directory), "click-native-observer-" + "0" * 32, records))
                    # The next decision sees the change.
                    self.assertFalse(observation_inputs.records_current(
                        Path(directory), "click-native-observer-" + "0" * 32, records))

    def test_a_root_match_is_whole_components_at_the_host_case_rule(self) -> None:
        from hooks import click_observation_inputs as observation_inputs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ("lib", "lib2"):
                (root / name).mkdir()
                (root / name / "inner.txt").write_text("", encoding="utf-8")
            snapshot = object.__new__(observation_inputs.InputSnapshot)
            snapshot._root_prefixes = {}
            snapshot.roots = {"project-parent": root, "project": root / "lib"}
            # The deepest matching root wins, and a sibling whose name merely
            # starts with the root's name belongs to the parent, not to it.
            self.assertEqual(snapshot.locator(root / "lib"), ("project", ""))
            self.assertEqual(snapshot.locator(root / "lib" / "inner.txt"), ("project", "inner.txt"))
            self.assertEqual(snapshot.locator(root / "lib2" / "inner.txt"),
                             ("project-parent", "lib2/inner.txt"))
            # A filesystem root already ends in the separator (a drive root on
            # Windows); its direct children still resolve against it.
            anchor = Path(root.anchor)
            first = root.relative_to(anchor).parts[0]
            snapshot.roots = {"host-root": anchor}
            snapshot._root_prefixes = {}
            self.assertEqual(snapshot.locator(anchor / first), ("host-root", first))
            # Nothing outside every root is locatable.
            snapshot.roots = {"project": root / "lib"}
            snapshot._root_prefixes = {}
            with self.assertRaises(observation_inputs.InputError):
                snapshot.locator(root / "lib2" / "inner.txt")

    def test_an_identity_pass_never_outlives_its_decision(self) -> None:
        from hooks import click_observation_inputs as observation_inputs

        self.assertIsNone(observation_inputs._identity_pass)
        with observation_inputs.identity_pass():
            self.assertIsNotNone(observation_inputs._identity_pass)
            outer = observation_inputs._identity_pass
            with observation_inputs.identity_pass():
                # A nested pass joins the decision already in progress.
                self.assertIs(observation_inputs._identity_pass, outer)
            self.assertIs(observation_inputs._identity_pass, outer)
        self.assertIsNone(observation_inputs._identity_pass)
        with self.assertRaises(RuntimeError):
            with observation_inputs.identity_pass():
                raise RuntimeError("failed decision")
        self.assertIsNone(observation_inputs._identity_pass)

    def test_child_process_requires_complete_process_tree_coverage(self) -> None:
        partial = click_dependency_cache.dependency_observation(
            ["src/unit.py"],
            child_processes=1,
            process_tree_complete=False,
        )
        followed = click_dependency_cache.dependency_observation(
            ["src/unit.py"],
            child_processes=1,
            process_tree_complete=True,
        )

        self.assertFalse(
            click_dependency_cache.dependency_observation_is_complete(partial)
        )
        self.assertTrue(
            click_dependency_cache.dependency_observation_is_complete(followed)
        )

        combined = click_dependency_cache.combine_dependency_observations(
            [
                followed,
                click_dependency_cache.dependency_observation(["docs/a.md"]),
            ]
        )
        self.assertEqual(combined["paths"], ["docs/a.md", "src/unit.py"])
        self.assertEqual(combined["child_processes"], 1)
        self.assertTrue(
            click_dependency_cache.dependency_observation_is_complete(combined)
        )

    def test_observed_missing_lookup_invalidates_when_the_path_appears(self) -> None:
        self.write_manifest([self.entry(["src/unit.py"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(
            ["optional.py"]
        )
        first = self.receipts(observations={"source": observation})["source"]

        (self.root / "optional.py").write_text("OPTION = 1\n", encoding="utf-8")
        appeared = self.receipts(observations={"source": observation})["source"]

        self.assertIn("optional.py", first["resolved_paths"])
        self.assertNotEqual(first["dependency_digest"], appeared["dependency_digest"])

    def test_observed_directory_listing_invalidates_membership_change(self) -> None:
        self.write_manifest([self.entry(["src/unit.py"])])
        self.commit()
        observation = click_dependency_cache.dependency_observation(["src/"])
        first = self.receipts(observations={"source": observation})["source"]

        (self.root / "src" / "added.py").write_text(
            "ADDED = 1\n", encoding="utf-8"
        )
        changed = self.receipts(observations={"source": observation})["source"]

        self.assertNotEqual(first["dependency_digest"], changed["dependency_digest"])


if __name__ == "__main__":
    unittest.main()
