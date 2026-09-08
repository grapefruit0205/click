from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hooks import click_runtime_identity as identity


class RuntimeIdentityTests(unittest.TestCase):
    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def fixture(self, root: Path) -> dict[str, Path]:
        tools = root / "tools"
        tools.mkdir()
        result: dict[str, Path] = {}
        for name in (
            "node", "go", "npx", "npm", "cargo", "rustc", "java",
            "gradle", "mvn", "dotnet",
        ):
            path = tools / name
            path.write_text(f"{name}-runtime", encoding="utf-8")
            result[name] = path
        return result

    def test_node_runtime_and_project_config_are_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            package = root / "package.json"
            package.write_text('{"type":"module"}', encoding="utf-8")
            resolve = lambda name: tools.get(name)
            first = identity.collect(
                ["node", "--test", "test/example.test.mjs"],
                cwd=root,
                environment={},
                resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(first["status"], "complete")
            package.write_text('{"type":"commonjs"}', encoding="utf-8")
            second = identity.collect(
                ["node", "--test", "test/example.test.mjs"],
                cwd=root,
                environment={},
                resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertNotEqual(first["digest"], second["digest"])

    def test_package_lifecycle_and_install_state_disable_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {
                            "pretest": "node preflight.mjs",
                            "test": "node --test test/example.test.mjs",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "node_modules").mkdir()
            binding = identity.collect(
                ["npm", "test"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )
            self.assertEqual(binding["status"], "incomplete")
            self.assertIn(
                "package-lifecycle-script-present", binding["reason_codes"]
            )
            self.assertIn("node-install-state-unbound", binding["reason_codes"])

    def test_unprovisioned_package_runner_is_unsafe_without_reading_project_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            binding = identity.collect(
                ["npx", "missing-test-runner", "--run"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )
            self.assertEqual(binding["status"], "unsafe")
            self.assertEqual(
                binding["reason_codes"], ["package-runner-download-unapproved"]
            )
            rendered = json.dumps(binding, sort_keys=True)
            self.assertNotIn(str(root), rendered)

    def test_no_install_package_runner_is_bound_to_its_local_manifest_and_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            package_root = root / "node_modules" / "vitest"
            package_root.mkdir(parents=True)
            (package_root / "package.json").write_text(
                json.dumps({"name": "vitest", "bin": {"vitest": "vitest.mjs"}}),
                encoding="utf-8",
            )
            (package_root / "vitest.mjs").write_text(
                "export default true\n", encoding="utf-8"
            )
            bin_root = root / "node_modules" / ".bin"
            bin_root.mkdir()
            launcher = bin_root / ("vitest.cmd" if identity.os.name == "nt" else "vitest")
            launcher.write_text("local launcher\n", encoding="utf-8")
            transitive = root / "node_modules" / "dependency" / "index.js"
            transitive.parent.mkdir()
            transitive.write_text("export default true\n", encoding="utf-8")
            binding = identity.collect(
                ["npx", "--no-install", "vitest", "run"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )

            self.assertEqual(binding["status"], "complete", binding)
            cache = root / "node_modules" / ".vite" / "vitest" / "results.json"
            cache.parent.mkdir(parents=True)
            cache.write_text("derived cache\n", encoding="utf-8")
            cached = identity.collect(
                ["npx", "--no-install", "vitest", "run"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )
            self.assertEqual(binding["digest"], cached["digest"])
            transitive.write_text(
                "export default false\n", encoding="utf-8"
            )
            changed = identity.collect(
                ["npx", "--no-install", "vitest", "run"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )
            self.assertNotEqual(binding["digest"], changed["digest"])

    def test_package_runner_target_cannot_escape_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            binding = identity.collect(
                ["npx", "--no-install", "../vitest", "run"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )

            self.assertEqual(binding["status"], "unsafe")
            self.assertEqual(
                binding["reason_codes"], ["package-runner-download-unapproved"]
            )

    def test_package_lock_and_installed_lock_mismatch_is_not_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "node --test test.js"}}),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "lockfileVersion": 3,
                        "packages": {"node_modules/tool": {"version": "1.0.0"}},
                    }
                ),
                encoding="utf-8",
            )
            installed = root / "node_modules"
            installed.mkdir()
            (installed / ".package-lock.json").write_text(
                json.dumps(
                    {
                        "lockfileVersion": 3,
                        "packages": {"node_modules/tool": {"version": "2.0.0"}},
                    }
                ),
                encoding="utf-8",
            )
            binding = identity.collect(
                ["npm", "test"],
                cwd=root,
                environment={},
                resolve_executable=lambda name: tools.get(name),
                digest_file=self.digest,
            )
            self.assertEqual(binding["status"], "incomplete")
            self.assertIn("node-lock-install-mismatch", binding["reason_codes"])

    def test_go_toolchain_download_is_blocked_but_local_mode_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            (root / "go.mod").write_text(
                "module example.invalid/click\n\ngo 1.22\ntoolchain go1.99.0\n",
                encoding="utf-8",
            )
            resolve = lambda name: tools.get(name)
            automatic = identity.collect(
                ["go", "test", "./..."],
                cwd=root,
                environment={},
                resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(automatic["status"], "unsafe")
            self.assertIn(
                "go-toolchain-download-unapproved", automatic["reason_codes"]
            )
            local_toolchain_only = identity.collect(
                ["go", "test", "./..."],
                cwd=root,
                environment={"GOTOOLCHAIN": "local"},
                resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(local_toolchain_only["status"], "unsafe")
            self.assertIn(
                "go-module-download-unapproved",
                local_toolchain_only["reason_codes"],
            )
            local = identity.collect(
                ["go", "test", "./..."],
                cwd=root,
                environment={"GOTOOLCHAIN": "local", "GOPROXY": "off"},
                resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(local["status"], "complete")

    def test_cargo_requires_offline_mode_and_rejects_rustup_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "click-fixture"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            resolve = lambda name: tools.get(name)
            online = identity.collect(
                ["cargo", "test"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(online["status"], "unsafe")
            self.assertIn(
                "cargo-registry-download-unapproved", online["reason_codes"]
            )
            offline = identity.collect(
                ["cargo", "test", "--offline"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(offline["status"], "complete")
            (root / "rust-toolchain.toml").write_text(
                '[toolchain]\nchannel = "stable"\n', encoding="utf-8"
            )
            override = identity.collect(
                ["cargo", "test", "--offline"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(override["status"], "unsafe")
            self.assertIn(
                "rust-toolchain-download-unapproved", override["reason_codes"]
            )

    def test_jvm_and_dotnet_require_no_download_execution_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            resolve = lambda name: tools.get(name)
            gradle = identity.collect(
                ["gradle", "test"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(gradle["status"], "unsafe")
            offline_gradle = identity.collect(
                ["gradle", "test", "--offline"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(offline_gradle["status"], "complete")
            wrapper = identity.collect(
                ["./gradlew", "test", "--offline"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(wrapper["status"], "unsafe")
            self.assertIn("wrapper-download-unapproved", wrapper["reason_codes"])

            dotnet = identity.collect(
                ["dotnet", "test"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(dotnet["status"], "unsafe")
            no_restore = identity.collect(
                ["dotnet", "test", "--no-restore"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertEqual(no_restore["status"], "complete")

    def test_remote_schema_is_blocked_and_local_validation_config_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = self.fixture(root)
            tools["check-jsonschema"] = tools["node"]
            config = root / ".yamllint"
            config.write_text("extends: default\n", encoding="utf-8")
            resolve = lambda name: tools.get(name)
            local = identity.collect(
                ["yamllint", "config.yaml"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            config.write_text("extends: relaxed\n", encoding="utf-8")
            changed = identity.collect(
                ["yamllint", "config.yaml"], cwd=root, environment={},
                resolve_executable=resolve, digest_file=self.digest,
            )
            self.assertNotEqual(local["digest"], changed["digest"])
            remote = identity.collect(
                [
                    "check-jsonschema", "--schemafile",
                    "https://example.invalid/schema.json", "config.yaml",
                ],
                cwd=root, environment={}, resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(remote["status"], "unsafe")
            self.assertIn(
                "external-schema-fetch-unapproved", remote["reason_codes"]
            )
            bounded = identity.collect(
                [
                    "check-jsonschema", "--schemafile", "schema.json",
                    "config.yaml",
                ],
                cwd=root, environment={}, resolve_executable=resolve,
                digest_file=self.digest,
            )
            self.assertEqual(bounded["status"], "unsafe")
            self.assertIn(
                "schema-reference-network-unbounded", bounded["reason_codes"]
            )


if __name__ == "__main__":
    unittest.main()
