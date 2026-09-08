from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest

from tests.click_gate_test_support import ClickGateTestCase, split_runner_command


NODE = shutil.which("node")
NPM = shutil.which("npm")
JQ = shutil.which("jq")


VALIDATOR = r"""
import fs from 'node:fs';
import path from 'node:path';

function fail(message) {
  console.error(message);
  process.exit(1);
}

function read(relative) {
  return fs.readFileSync(relative, 'utf8');
}

function yamlMapping(relative) {
  const result = {};
  for (const raw of read(relative).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const match = /^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)$/.exec(line);
    if (!match || !match[2]) fail('unsupported or invalid YAML mapping');
    result[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return result;
}

const [kind, source, reference] = process.argv.slice(2);
if (kind === 'json') {
  const value = JSON.parse(read(source));
  const schema = JSON.parse(read(reference));
  for (const key of schema.required || []) {
    if (!(key in value)) fail(`missing JSON key: ${key}`);
  }
} else if (kind === 'yaml') {
  const value = yamlMapping(source);
  const schema = JSON.parse(read(reference));
  for (const key of schema.required || []) {
    if (!(key in value)) fail(`missing YAML key: ${key}`);
  }
} else if (kind === 'markdown') {
  for (const match of read(source).matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].split('#', 1)[0];
    if (!target || /^(https?:|mailto:)/i.test(target)) continue;
    const resolved = path.resolve(path.dirname(source), decodeURIComponent(target));
    if (!fs.existsSync(resolved)) fail(`missing local link target: ${target}`);
  }
} else if (kind === 'svg') {
  const value = read(source);
  if (!/^\s*<svg\b/i.test(value)) fail('not an SVG document');
  const width = /\bwidth=["']([0-9]+)["']/.exec(value);
  const height = /\bheight=["']([0-9]+)["']/.exec(value);
  if (!width || !height || Number(width[1]) < 1 || Number(height[1]) < 1) {
    fail('SVG dimensions are missing or invalid');
  }
  for (const match of value.matchAll(/\bhref=["']([^"']+)["']/g)) {
    const target = match[1];
    if (/^(#|data:|https?:)/i.test(target)) continue;
    if (!fs.existsSync(path.resolve(path.dirname(source), target))) {
      fail(`missing SVG reference: ${target}`);
    }
  }
} else {
  fail('unknown fixture validation profile');
}
""".lstrip()


class ContentValidationHookTests(ClickGateTestCase):
    def assert_runner(self, payload: dict, expected: bool = True) -> None:
        output = payload["hookSpecificOutput"]
        present = "updatedInput" in output and "run-verification" in split_runner_command(
            output["updatedInput"]["command"]
        )
        self.assertEqual(present, expected)

    def check(self, command: list[str], inputs: list[str], *, profile: str = "broad") -> dict:
        return {
            "argv": command,
            "class": profile,
            "reuse": "conditional",
            "inputs": inputs,
            "outputs_required": False,
        }

    def mutate(self, path: Path, content: str, tool_id: str) -> None:
        self.assertIsNone(
            self.pre_tool(
                "apply_patch", "*** Begin Patch\n*** End Patch", "turn-1",
                submit_prompt=False, tool_use_id=tool_id,
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.tool_hook(
            "post-tool", "apply_patch", {"patch": f"changed {path.name}"},
            turn_id="turn-1", tool_use_id=tool_id,
        )

    def delete(self, path: Path, tool_id: str) -> None:
        self.assertIsNone(
            self.pre_tool(
                "apply_patch", "*** Begin Patch\n*** End Patch", "turn-1",
                submit_prompt=False, tool_use_id=tool_id,
            )
        )
        path.unlink()
        self.tool_hook(
            "post-tool", "apply_patch", {"patch": f"deleted {path.name}"},
            turn_id="turn-1", tool_use_id=tool_id,
        )

    def content_project(self) -> Path:
        project = self.workspace / "검증 project"
        for directory in ("validators", "data", "schemas", "docs", "assets/generated"):
            (project / directory).mkdir(parents=True, exist_ok=True)
        (project / ".gitignore").write_text(
            "node_modules/\nassets/generated/\n", encoding="utf-8"
        )
        (project / "validators" / "validate.mjs").write_text(
            VALIDATOR, encoding="utf-8"
        )
        scripts = {
            "validate:json": (
                "node validators/validate.mjs json data/settings.json "
                "schemas/settings.schema.json"
            ),
            "validate:yaml": (
                "node validators/validate.mjs yaml data/app.yaml "
                "schemas/app.schema.json"
            ),
            "validate:markdown": (
                "node validators/validate.mjs markdown docs/guide.md"
            ),
            "validate:asset": (
                "node validators/validate.mjs svg assets/card.svg"
            ),
        }
        (project / "package.json").write_text(
            json.dumps(
                {"name": "click-content-fixture", "private": True, "scripts": scripts},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        (project / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "click-content-fixture",
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {"": {"name": "click-content-fixture"}},
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        (project / "data" / "settings.json").write_text(
            '{"name":"click","enabled":true}\n', encoding="utf-8"
        )
        (project / "schemas" / "settings.schema.json").write_text(
            '{"required":["name","enabled"]}\n', encoding="utf-8"
        )
        (project / "data" / "app.yaml").write_text(
            "name: click\nport: 9000\n", encoding="utf-8"
        )
        (project / "schemas" / "app.schema.json").write_text(
            '{"required":["name","port"]}\n', encoding="utf-8"
        )
        (project / "docs" / "target page.md").write_text(
            "# Target\n", encoding="utf-8"
        )
        (project / "docs" / "guide.md").write_text(
            "# Guide\n\n[local](target%20page.md)\n"
            "[remote not checked](https://example.invalid/)\n",
            encoding="utf-8",
        )
        (project / "assets" / "generated" / "texture.bin").write_text(
            "first\n", encoding="utf-8"
        )
        (project / "assets" / "card.svg").write_text(
            '<svg width="32" height="18" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<image href="generated/texture.bin"/></svg>\n',
            encoding="utf-8",
        )
        self.workspace = project
        self.base_event["cwd"] = str(project)
        self.initialize_git(
            ".gitignore", "validators", "package.json", "package-lock.json",
            "data", "schemas", "docs", "assets/card.svg",
        )
        return project

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_json_schema_validation_reuses_and_reruns_on_content_change(self) -> None:
        project = self.content_project()
        command = ["npm", "run", "validate:json", "--silent"]
        inputs = ["data/settings.json", "schemas/settings.schema.json"]
        first = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assert_runner(self.verify_checks([self.check(command, inputs)], "turn-1", version=3), False)

        schema = project / "schemas" / "settings.schema.json"
        self.mutate(schema, '{"required":["name","enabled","region"]}\n', "json-schema")
        failing = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertNotEqual(self.run_rewritten(failing).returncode, 0)
        settings = project / "data" / "settings.json"
        self.mutate(settings, '{"name":"click","enabled":true,"region":"kr"}\n', "json-fix")
        fixed = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(fixed).returncode, 0)

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_yaml_validation_binds_shared_schema_and_missing_reference(self) -> None:
        project = self.content_project()
        command = ["npm", "run", "validate:yaml", "--silent"]
        inputs = ["data/app.yaml", "schemas/app.schema.json"]
        first = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assert_runner(self.verify_checks([self.check(command, inputs)], "turn-1", version=3), False)

        schema = project / "schemas" / "app.schema.json"
        self.delete(schema, "yaml-schema-delete")
        missing = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertNotEqual(self.run_rewritten(missing).returncode, 0)
        self.mutate(schema, '{"required":["name","port"]}\n', "yaml-schema-restore")
        restored = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(restored).returncode, 0)

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_markdown_local_links_ignore_remote_state_but_track_local_targets(self) -> None:
        project = self.content_project()
        command = ["npm", "run", "validate:markdown", "--silent"]
        inputs = ["docs/guide.md", "docs/target page.md"]
        first = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assert_runner(self.verify_checks([self.check(command, inputs)], "turn-1", version=3), False)

        target = project / "docs" / "target page.md"
        self.delete(target, "markdown-target-delete")
        missing = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertNotEqual(self.run_rewritten(missing).returncode, 0)
        self.mutate(target, "# Restored\n", "markdown-target-restore")
        restored = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(restored).returncode, 0)

    @unittest.skipUnless(NODE and NPM, "Node and npm are not installed")
    def test_svg_dimensions_and_ignored_reference_are_explicit_inputs(self) -> None:
        project = self.content_project()
        command = ["npm", "run", "validate:asset", "--silent"]
        inputs = ["assets/card.svg", "assets/generated/texture.bin"]
        first = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assert_runner(self.verify_checks([self.check(command, inputs)], "turn-1", version=3), False)

        ignored = project / "assets" / "generated" / "texture.bin"
        self.mutate(ignored, "second\n", "asset-ignored-change")
        changed = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assert_runner(changed)
        self.assertEqual(self.run_rewritten(changed).returncode, 0)
        svg = project / "assets" / "card.svg"
        self.mutate(
            svg,
            '<svg width="0" height="18" xmlns="http://www.w3.org/2000/svg"/>\n',
            "asset-dimension-fail",
        )
        failing = self.verify_checks([self.check(command, inputs)], "turn-1", version=3)
        self.assertNotEqual(self.run_rewritten(failing).returncode, 0)

    @unittest.skipUnless(JQ, "jq is not installed")
    def test_real_jq_parser_profile_accepts_no_write_or_eval_shape(self) -> None:
        project = self.content_project()
        command = ["jq", "empty", "data/settings.json"]
        check = self.check(command, ["data/settings.json"], profile="targeted")
        first = self.verify_checks([check], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        self.assert_runner(self.verify_checks([check], "turn-1", version=3), False)
        settings = project / "data" / "settings.json"
        self.mutate(settings, '{"private":"value-without-output"\n', "jq-invalid")
        failing = self.verify_checks([check], "turn-1", version=3)
        self.assertNotEqual(self.run_rewritten(failing).returncode, 0)
        self.assertNotIn("value-without-output", failing["hookSpecificOutput"].get("additionalContext", ""))

    @unittest.skipUnless(JQ, "jq is not installed")
    def test_oversized_explicit_input_runs_but_never_becomes_reusable(self) -> None:
        project = self.content_project()
        settings = project / "data" / "settings.json"
        self.mutate(
            settings,
            json.dumps({"payload": "x" * (9 * 1024 * 1024)}) + "\n",
            "json-oversized",
        )
        command = ["jq", "empty", "data/settings.json"]
        check = self.check(command, ["data/settings.json"], profile="targeted")
        first = self.verify_checks([check], "turn-1", version=3)
        self.assertEqual(self.run_rewritten(first).returncode, 0)
        second = self.verify_checks([check], "turn-1", version=3)
        self.assert_runner(second)
        state_path = next(
            (self.plugin_data / "gate-state").glob("session-contract-*.json")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        decision = state["verification"]["incremental_plan"]["decisions"][0]
        self.assertEqual(decision["reason_code"], "explicit-input-unavailable")


if __name__ == "__main__":
    unittest.main()
