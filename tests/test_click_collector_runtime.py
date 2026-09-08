from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from hooks import click_collector_runtime as runtime
from hooks import click_pytest_collector


class ClickCollectorRuntimeTests(unittest.TestCase):
    def test_capabilities_are_explicit_for_supported_and_unknown_systems(self) -> None:
        for system in ("Linux", "Darwin", "Windows"):
            with self.subTest(system=system):
                capability = runtime.capability(system)
                self.assertEqual(capability.status, "implemented")
                self.assertEqual(capability.reason, "runtime-validation-required")
        self.assertEqual(runtime.capability("OtherOS").status, "unsupported")

    def test_cpython_support_range_is_bounded(self) -> None:
        self.assertTrue(runtime.cpython_supported("cpython", (3, 10, 0)))
        self.assertTrue(runtime.cpython_supported("cpython", (3, 14, 99)))
        self.assertFalse(runtime.cpython_supported("cpython", (3, 9, 99)))
        self.assertFalse(runtime.cpython_supported("cpython", (3, 15, 0)))
        self.assertFalse(runtime.cpython_supported("pypy", (3, 12, 3)))

    @unittest.skipUnless(
        runtime.capability().status == "implemented", "unsupported local platform"
    )
    def test_supervisor_bounds_output_and_time_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary)
            runtime.supervise(
                [sys.executable, "-c", "pass"],
                cwd,
                dict(os.environ),
                timeout=2,
                output_bytes=1024,
            )
            with self.assertRaisesRegex(
                runtime.CollectorRuntimeError, "collection-output-limit"
            ):
                runtime.supervise(
                    [sys.executable, "-c", "import os; os.write(1, b'x' * 8192)"],
                    cwd,
                    dict(os.environ),
                    timeout=2,
                    output_bytes=128,
                )
            with self.assertRaisesRegex(
                runtime.CollectorRuntimeError, "collection-timeout"
            ):
                runtime.supervise(
                    [sys.executable, "-c", "while True: pass"],
                    cwd,
                    dict(os.environ),
                    timeout=0.05,
                    output_bytes=1024,
                )

    def test_supervisor_can_return_bounded_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            captured = runtime.supervise(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'out'); os.write(2, b'err')",
                ],
                Path(temporary),
                dict(os.environ),
                timeout=2,
                output_bytes=1024,
                capture_output=True,
            )

        self.assertEqual(captured, (b"out", b"err"))

    def test_pytest_worker_canonicalizes_collected_items_without_running_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tests" / "nested" / "test_case.py"
            source.parent.mkdir(parents=True)
            source.write_text("def test_value():\n    assert True\n", encoding="utf-8")
            item = types.SimpleNamespace(
                nodeid="tests/nested/test_case.py::test_value[param]",
                path=source,
                module=types.SimpleNamespace(__name__="tests.nested.test_case"),
                cls=None,
                originalname="test_value",
                name="test_value[param]",
                _fixtureinfo=types.SimpleNamespace(name2fixturedefs={}),
            )

            def fake_main(arguments, plugins):
                self.assertIn("--collect-only", arguments)
                cache_option = arguments.index("-p")
                self.assertEqual(arguments[cache_option + 1], "no:cacheprovider")
                session = types.SimpleNamespace(
                    config=types.SimpleNamespace(
                        getini=lambda name: ("test_*.py", "*_test.py")
                    ),
                    items=[item],
                )
                plugins[0].pytest_collection_finish(session)
                return 0

            fake_pytest = types.SimpleNamespace(__version__="8.3.1", main=fake_main)
            with mock.patch.dict(sys.modules, {"pytest": fake_pytest}):
                result = click_pytest_collector.collect(["-q", "tests"], root, root)
            self.assertEqual(result["reasons"], [])
            self.assertEqual(result["runtime"]["framework_version"], "8.3.1")
            self.assertEqual(
                result["tests"][0]["id"],
                "tests/nested/test_case.py::test_value[param]",
            )
if __name__ == "__main__":
    unittest.main()
