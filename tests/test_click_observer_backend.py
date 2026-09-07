from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest import mock

from hooks import (
    click_dependency_cache,
    click_dependency_trace,
    click_observer_backend,
    click_observer_common,
    click_observer_linux,
    click_observer_macos,
    click_observer_windows,
)


EVIDENCE_KEY = "a" * 64
CHECK_DIGEST = "b" * 64


class ClickObserverBackendTests(unittest.TestCase):
    def test_selector_exposes_all_three_native_backends(self) -> None:
        linux = click_observer_backend.select_backend("Linux")
        self.assertEqual(linux.system, "Linux")
        self.assertEqual(linux.backend_name, "strace")
        self.assertEqual(linux.status, "available")
        self.assertEqual(linux.reason, "runtime-probe-required")

        macos = click_observer_backend.select_backend(
            "Darwin", macos_privileged=False
        )
        self.assertEqual(macos.system, "Darwin")
        self.assertEqual(macos.backend_name, "fs_usage")
        self.assertEqual(macos.status, "permission-required")
        self.assertEqual(macos.reason, "root-privilege-required")

        privileged_macos = click_observer_backend.select_backend(
            "Darwin", macos_privileged=True
        )
        self.assertEqual(privileged_macos.backend_name, "fs_usage")
        self.assertEqual(privileged_macos.status, "available")
        self.assertEqual(privileged_macos.reason, "runtime-probe-required")

        windows = click_observer_backend.select_backend("Windows")
        self.assertEqual(windows.system, "Windows")
        self.assertEqual(windows.backend_name, "windows-etw")
        self.assertEqual(windows.status, "available")
        self.assertEqual(windows.reason, "runtime-probe-required")

        unknown = click_observer_backend.select_backend("OtherOS")
        self.assertIsNone(unknown.backend_name)
        self.assertEqual(unknown.status, "unavailable")
        self.assertEqual(unknown.reason, "unsupported-operating-system")

    def test_capability_contract_rejects_contradictory_states(self) -> None:
        with self.assertRaises(ValueError):
            click_observer_backend.BackendCapability(
                system="Darwin",
                backend_name=None,
                status="available",
                reason="invalid",
            )
        with self.assertRaises(ValueError):
            click_observer_backend.BackendCapability(
                system="Windows",
                backend_name="invented",
                status="unavailable",
                reason="invalid",
            )
        with self.assertRaises(ValueError):
            click_observer_backend.BackendCapability(
                system="OtherOS",
                backend_name=None,
                status="unknown",
                reason="invalid",
            )

    def test_support_report_separates_common_shadow_and_authoritative_tiers(self) -> None:
        linux = click_observer_backend.support_report("Linux")
        self.assertEqual(linux["base_reuse"]["status"], "implemented")
        self.assertEqual(linux["static_configuration"]["status"], "implemented")
        self.assertEqual(linux["shadow_observer"]["backend"], "strace")
        self.assertEqual(
            linux["authoritative_observer"]["status"], "profile-implemented"
        )

        for system in ("Darwin", "Windows"):
            with self.subTest(system=system):
                report = click_observer_backend.support_report(
                    system, macos_privileged=False
                )
                self.assertEqual(report["base_reuse"]["status"], "implemented")
                self.assertEqual(report["static_configuration"]["status"], "implemented")
                self.assertEqual(
                    report["authoritative_observer"]["status"],
                    "profile-implemented",
                )
                self.assertIn(
                    "real-host-validation-required",
                    report["authoritative_observer"]["reason"],
                )
    def test_unavailable_facade_executes_target_once_without_backend_probe(self) -> None:
        calls: list[str] = []
        for system in ("Darwin", "OtherOS"):
            with self.subTest(system=system):
                result = click_dependency_trace.run_command(
                    ["check", "--flag"],
                    workspace=Path.cwd(),
                    environment={},
                    evidence_key=EVIDENCE_KEY,
                    check_digest=CHECK_DIGEST,
                    mutation_revision=2,
                    execute_unobserved=lambda: calls.append(system) or 7,
                    resolve_backend=lambda *_args, **_kwargs: self.fail(
                        "an unavailable backend must not be probed"
                    ),
                    system_name=system,
                    macos_privilege_probe=lambda: False,
                )
                self.assertEqual(result.exit_code, 7)
                self.assertEqual(result.record["status"], "unavailable")
                self.assertIsNone(result.record["backend"])
                self.assertFalse(result.record["authoritative"])
                self.assertFalse(result.record["reuse_authorized"])
        self.assertEqual(calls, ["Darwin", "OtherOS"])

    def test_capability_detection_failure_executes_target_once(self) -> None:
        calls: list[int] = []
        with mock.patch.object(
            click_dependency_trace,
            "select_backend",
            side_effect=RuntimeError("probe failed"),
        ):
            result = click_dependency_trace.run_command(
                ["check"],
                workspace=Path.cwd(),
                environment={},
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=3,
                execute_unobserved=lambda: calls.append(1) or 4,
                resolve_backend=lambda *_args, **_kwargs: self.fail(
                    "failed capability detection must not probe a backend"
                ),
                system_name="Linux",
            )
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(calls, [1])
        self.assertEqual(result.record["status"], "unavailable")

    def test_linux_facade_dispatches_through_backend_boundary(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=4,
            backend_name="strace",
            backend_version="6.1",
            backend_digest="c" * 64,
        )
        expected = click_observer_common.ShadowExecution(0, record)
        fallback = mock.Mock(return_value=91)
        with mock.patch.object(
            click_observer_linux, "run_command", return_value=expected
        ) as run:
            result = click_dependency_trace.run_command(
                ["check"],
                workspace=Path.cwd(),
                environment={},
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=4,
                execute_unobserved=fallback,
                resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/strace", ""),
                system_name="Linux",
            )
        self.assertIs(result, expected)
        self.assertEqual(run.call_count, 1)
        fallback.assert_not_called()

    def test_macos_facade_dispatches_through_backend_boundary(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=5,
            backend_name="fs_usage",
            backend_version="15.0",
            backend_digest="c" * 64,
            status="partial",
            unresolved_event_count=1,
            process_tree_complete=False,
        )
        expected = click_observer_common.ShadowExecution(0, record)
        fallback = mock.Mock(return_value=91)
        collector = mock.Mock()
        with mock.patch.object(
            click_observer_macos, "run_command", return_value=expected
        ) as run:
            result = click_dependency_trace.run_command(
                ["check"],
                workspace=Path.cwd(),
                environment={},
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=5,
                execute_unobserved=fallback,
                resolve_backend=lambda *_args, **_kwargs: ("/usr/bin/fs_usage", ""),
                system_name="Darwin",
                macos_privilege_probe=lambda: True,
                macos_collector=collector,
            )
        self.assertIs(result, expected)
        self.assertEqual(run.call_count, 1)
        self.assertIs(run.call_args.kwargs["collector"], collector)
        fallback.assert_not_called()

    def test_windows_facade_dispatches_through_backend_boundary(self) -> None:
        record = click_dependency_cache.shadow_observer_record(
            evidence_key=EVIDENCE_KEY,
            check_digest=CHECK_DIGEST,
            mutation_revision=6,
            backend_name="windows-etw",
            backend_version="10.0.26100",
            backend_digest="c" * 64,
            status="partial",
            unresolved_event_count=1,
            process_tree_complete=False,
        )
        expected = click_observer_common.ShadowExecution(0, record)
        fallback = mock.Mock(return_value=91)
        collector = mock.Mock()
        with mock.patch.object(
            click_observer_windows, "run_command", return_value=expected
        ) as run:
            result = click_dependency_trace.run_command(
                ["check"],
                workspace=Path.cwd(),
                environment={},
                evidence_key=EVIDENCE_KEY,
                check_digest=CHECK_DIGEST,
                mutation_revision=6,
                execute_unobserved=fallback,
                resolve_backend=lambda *_args, **_kwargs: (
                    "C:/Windows/System32/logman.exe",
                    "",
                ),
                system_name="Windows",
                windows_collector=collector,
            )
        self.assertIs(result, expected)
        self.assertEqual(run.call_count, 1)
        self.assertIs(run.call_args.kwargs["collector"], collector)
        fallback.assert_not_called()

    def test_backend_layers_do_not_import_authority_domains(self) -> None:
        forbidden = {
            "click_contract",
            "click_evidence",
            "click_gate",
            "click_receipt",
            "click_state",
            "click_verification",
        }
        for module in (
            click_observer_backend,
            click_observer_common,
            click_observer_linux,
            click_observer_macos,
            click_observer_windows,
        ):
            with self.subTest(module=module.__name__):
                tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
                imported: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                self.assertTrue(forbidden.isdisjoint(imported), imported)


if __name__ == "__main__":
    unittest.main()
