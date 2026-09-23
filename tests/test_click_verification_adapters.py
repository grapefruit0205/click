from __future__ import annotations

from pathlib import Path
import unittest

from hooks import (
    click_evidence_shards,
    click_test_inventory,
    click_verification_adapters,
    click_verification_plan,
)


class VerificationAdapterContractTests(unittest.TestCase):
    def test_registry_is_static_non_authoritative_and_capabilities_are_independent(
        self,
    ) -> None:
        identifiers = list(click_verification_adapters.DESCRIPTORS)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn(click_verification_adapters.UNITTEST_ADAPTER, identifiers)
        self.assertIn("vitest-v1", identifiers)
        self.assertIn("content-validation-v1", identifiers)

        unittest_report = click_verification_adapters.capability_report(
            click_verification_adapters.UNITTEST_ADAPTER
        )
        assert unittest_report is not None
        self.assertFalse(unittest_report["authority"])
        self.assertTrue(unittest_report["candidate_only"])
        self.assertEqual(
            unittest_report["capabilities"]["inventory"]["implementation"],
            "implemented",
        )
        self.assertEqual(
            unittest_report["capabilities"]["inventory"]["environment_test"],
            "not-recorded",
        )

        vitest_report = click_verification_adapters.capability_report("vitest-v1")
        assert vitest_report is not None
        self.assertEqual(
            vitest_report["capabilities"]["execute"]["implementation"],
            "implemented",
        )
        self.assertEqual(
            vitest_report["capabilities"]["inventory"]["implementation"],
            "profile-limited",
        )
        self.assertTrue(
            click_verification_adapters.supports("vitest-v1", "split")
        )
        self.assertTrue(
            click_verification_adapters.supports(
                "vitest-v1", "dependency_candidates"
            )
        )

    def test_command_profiles_preserve_existing_minimum_classification(self) -> None:
        cases = (
            (["python3", "-m", "unittest", "discover"], "cpython-unittest-v1", "broad"),
            (["python", "-m", "unittest", "tests.test_case"], "cpython-unittest-v1", "targeted"),
            (["py.exe", "-3.13", "-m", "pytest", "-q", "Tests/Test_Case.py"], "cpython-pytest-v1", "targeted"),
            ([r"C:\Python313\python.exe", "-m", "pytest", "tests"], "cpython-pytest-v1", "broad"),
            (["node", "--test", "test/case.test.js"], "node-test-v1", "targeted"),
            (["node", "node_modules/jest/bin/jest.js", "--runTestsByPath", "test/case.test.js"], "jest-v1", "targeted"),
            ([r"C:\repo\node_modules\vitest\vitest.mjs", "run"], "generic-named-verification-v1", "deep"),
            ([r"C:\Program Files\nodejs\node.EXE", r"C:\repo\node_modules\vitest\vitest.mjs", "run"], "vitest-v1", "broad"),
            (["npm", "run", "test"], "javascript-package-script-v1", "broad"),
            (["npx", "vitest", "test/case.test.ts"], "vitest-v1", "targeted"),
            (["cargo", "test", "core"], "cargo-v1", "targeted"),
            (["go", "test", "./..."], "go-v1", "broad"),
            (["ruff", "check", "src/App.py"], "python-static-v1", "targeted"),
            (["tsc", "--noEmit"], "typescript-v1", "broad"),
            (["dotnet", "test", "--filter", "Fast"], "dotnet-v1", "targeted"),
            ([r"C:\repo\gradlew.bat", "test"], "jvm-v1", "broad"),
            (["make", "test"], "native-build-v1", "broad"),
            (["jq", "empty", "config.json"], "content-validation-v1", "targeted"),
            (["yamllint", "config/app.yaml"], "content-validation-v1", "broad"),
            (["markdownlint", "docs/guide.md"], "content-validation-v1", "broad"),
            (["sqlfluff", "lint", "schema.sql"], "content-validation-v1", "broad"),
            (["xmllint", "--nonet", "--noout", "icon.svg"], "content-validation-v1", "broad"),
            (["identify", "-ping", "image.png"], "content-validation-v1", "broad"),
            (["npm", "run", "validate:yaml"], "content-validation-v1", "broad"),
            (["project-integration-test", "--all"], "generic-named-verification-v1", "deep"),
        )
        for argv, adapter_id, minimum_class in cases:
            with self.subTest(argv=argv):
                profile = click_verification_adapters.command_profile(argv)
                assert profile is not None
                self.assertEqual(profile["adapter_id"], adapter_id)
                self.assertEqual(profile["minimum_class"], minimum_class)
                self.assertEqual(
                    click_verification_plan.minimum_verification_class(argv),
                    minimum_class,
                )

    def test_unknown_command_has_no_capability_or_execution_authority(self) -> None:
        self.assertIsNone(
            click_verification_adapters.command_profile(
                ["python3", "-c", "print('not verification')"]
            )
        )
        # A script run by node is a check only at a framework's own entry point.
        for argv in (["node", "runner.js", "case.cjs"], ["node", "vitest.mjs", "run"],
                     ["node", "jest/bin/jest.js"], ["node", "node_modules/jest/bin/jest.js.bak"]):
            with self.subTest(argv=argv):
                self.assertIsNone(click_verification_adapters.command_profile(argv))
        self.assertIsNone(
            click_verification_adapters.capability_report("unknown-adapter")
        )
        self.assertFalse(
            click_verification_adapters.supports("unknown-adapter", "execute")
        )

    def test_content_validation_rejects_writes_and_network_dependent_shapes(self) -> None:
        rejected = (
            ["jq", ".", "private.json"],
            ["markdownlint", "--fix", "README.md"],
            ["sqlfluff", "fix", "schema.sql"],
            ["xmllint", "--format", "icon.svg"],
            ["xmllint", "--noout", "icon.svg"],
            ["identify", "-write", "copy.png", "image.png"],
        )
        for argv in rejected:
            with self.subTest(argv=argv):
                self.assertIsNone(
                    click_verification_adapters.command_profile(argv)
                )

    def test_common_model_preserves_original_paths_without_python_only_fields(
        self,
    ) -> None:
        model = click_verification_adapters.execution_model(
            adapter_id="node-test-v1",
            profile="node-test",
            original_argv=["Node.EXE", "--test", "Tests/Case.TEST.JS"],
            cwd="Packages/App With Spaces",
            execution_identity={"executable_digest": "1" * 64},
            inventory_units=[
                {
                    "kind": "file",
                    "id": "Tests/Case.TEST.JS",
                    "file": "Tests/Case.TEST.JS",
                }
            ],
            selector={"files": ["Tests/Case.TEST.JS"]},
            policy_digest="2" * 64,
            context_digest="3" * 64,
            unknown_reasons=["runtime-observation-unavailable"],
        )

        self.assertEqual(
            model["original_argv"],
            ["Node.EXE", "--test", "Tests/Case.TEST.JS"],
        )
        self.assertEqual(model["cwd"], "Packages/App With Spaces")
        self.assertNotIn("module", model["inventory_units"][0])
        self.assertFalse(model["authority"])

    def test_owner_committed_unknown_runner_shard_remains_compatible(self) -> None:
        raw = {
            "checks": [["custom-test-tool", "all"]],
            "inventory": ["tests/alpha.spec", "tests/beta.spec"],
            "shards": [
                {
                    "id": "alpha",
                    "checks": [["custom-test-tool", "alpha"]],
                    "covers": ["tests/alpha.spec"],
                },
                {
                    "id": "beta",
                    "checks": [["custom-test-tool", "beta"]],
                    "covers": ["tests/beta.spec"],
                },
            ],
        }
        normalized, error = click_evidence_shards._normalize_entry(
            raw,
            ["tests/alpha.spec", "tests/beta.spec"],
            working_prefix="",
            parent_digests=set(),
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(normalized)
        discovery, discovery_error = (
            click_verification_adapters.parent_discovery_paths(
                raw["checks"], raw["inventory"], working_prefix=""
            )
        )
        self.assertIsNone(discovery)
        self.assertEqual(discovery_error, "")

    def test_core_facades_route_through_adapter_boundary(self) -> None:
        planning = Path(click_verification_plan.__file__).read_text(encoding="utf-8")
        inventory_source = Path(click_test_inventory.__file__).read_text(
            encoding="utf-8"
        )
        adapter_source = Path(click_verification_adapters.__file__).read_text(
            encoding="utf-8"
        )

        self.assertIn("click_verification_adapters.minimum_verification_class", planning)
        self.assertNotIn('if executable == "go"', planning)
        self.assertNotIn("click_test_inventory", adapter_source)
        self.assertNotIn("click_dependency_candidates", adapter_source)
        self.assertNotIn(
            "click_dependency_candidates",
            "\n".join(inventory_source.splitlines()[:40]),
        )


class AutoRouteTests(unittest.TestCase):
    """Which plain Bash commands Evidence mode runs through `click-gate verify`."""

    def test_checks_route_and_keep_their_own_output_filters_verbatim(self) -> None:
        unittest_discover = ["python3", "-m", "unittest", "discover", "-s", "tests"]
        cases = {
            "python3 -m unittest discover -s tests": (unittest_discover, ""),
            "python3 -m pytest -q 2>&1 | tail -5": (["python3", "-m", "pytest", "-q"], "2>&1 | tail -5"),
            "pytest -q 2>&1": (["pytest", "-q"], "2>&1"),
            "pytest -q |& tail -20": (["pytest", "-q"], "|& tail -20"),
            "pytest -k 'a or (b)'": (["pytest", "-k", "a or (b)"], ""),
            "pytest 'x>y' 2>&1 | tail -3": (["pytest", "x>y"], "2>&1 | tail -3"),
            # The forms an unguided agent typed in docs/history/agent-ab-2026-09-12,
            # with the filter's own quoting preserved.
            'python3 -m unittest discover -s tests 2>&1 | grep -E "^(FAIL|ERROR):|^Ran" '
            "| sed 's/ (total.*//' | sort | uniq -c": (
                unittest_discover,
                '2>&1 | grep -E "^(FAIL|ERROR):|^Ran" | sed \'s/ (total.*//\' | sort | uniq -c',
            ),
            'npx vitest run | grep "$PATTERN"': (["npx", "vitest", "run"], '| grep "$PATTERN"'),
            # Every check Click recognizes, including ones that may write the
            # tree: a write is recorded as a host change, not a failure.
            "npm test": (["npm", "test"], ""),
            "npm run build 2>&1 | tail -20": (["npm", "run", "build"], "2>&1 | tail -20"),
            "python3 -m coverage run -m pytest": (
                ["python3", "-m", "coverage", "run", "-m", "pytest"], ""
            ),
            "jest -u": (["jest", "-u"], ""),
            "go test ./...": (["go", "test", "./..."], ""),
            "cargo check": (["cargo", "check"], ""),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(click_verification_plan.auto_route_argv(command), expected)

    def test_anything_the_argv_runner_would_not_reproduce_stays_with_the_host(self) -> None:
        for command in (
            # Not a check Click recognizes.
            "ls -la", "python3 -m http.server", "python3 script.py",
            # Shell semantics the argv runner has no equivalent for.
            "FOO=1 pytest", "pytest $ARGS", "pytest tests/test_*.py", "cd app && pytest",
            "pytest; ls", "pytest || true", "pytest &", "pytest > out.txt", "pytest 2>/dev/null",
            "pytest 2 >&1", "pytest 2>&1 | tail -3 > f", "pytest |", "pytest\nls",
            # Filters run under the same host decision as the check, so none
            # may write a file, run a command, or read another input.
            "pytest | tee log", "pytest | awk '{print $1}'", "pytest | xargs rm",
            'pytest | grep "$(rm x)"', "pytest | grep `x`", "pytest | sed -i s/a/b/ f",
            "pytest | sed s/a/b/ file.txt", "pytest | sed 's/a/b/w out'", "pytest | sed 's/a/b/e'",
            "pytest | sed -e s/a/b/ file", "pytest | uniq in out", "pytest | cat file",
            "pytest | sort -o f",
        ):
            with self.subTest(command=command):
                self.assertIsNone(click_verification_plan.auto_route_argv(command))

if __name__ == "__main__":
    unittest.main()
