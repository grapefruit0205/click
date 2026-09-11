from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
README_NAMES = ("README.md", "README.ko.md", "README.zh-CN.md")


def _imported_module_names(source: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(
                f"{module}.{alias.name}".strip(".") for alias in node.names
            )
    return imported


class RepositoryPolicyTests(unittest.TestCase):
    def test_json_artifacts_are_valid(self) -> None:
        for path in ROOT.rglob("*.json"):
            with self.subTest(path=path.relative_to(ROOT)):
                json.loads(path.read_text(encoding="utf-8"))

    def test_manifest_declares_click_one_shot_release(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "click")
        self.assertEqual(manifest["version"], "0.24.5")
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("always on", manifest["description"].lower())
        self.assertIn("manual", manifest["description"].lower())
        self.assertIn("plain-language", manifest["description"].lower())
        self.assertIn("execution contract", manifest["description"].lower())
        self.assertIn("compact", manifest["description"].lower())
        self.assertIn("one shot", manifest["interface"]["longDescription"].lower())
        self.assertIn("verification", manifest["interface"]["longDescription"].lower())
        self.assertIn(
            "cheapest sufficient primary evidence",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn("automatically", manifest["interface"]["longDescription"].lower())
        self.assertIn(
            "plan tools and distinct-digest broad inventories remain available with non-blocking advisory guidance",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn(
            "cannot alter contract authority",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn(
            "isolated child process groups",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn(
            "process-control executables",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn("contract_id", manifest["interface"]["longDescription"])
        self.assertIn(
            "never the json again",
            manifest["interface"]["longDescription"].lower(),
        )
        self.assertIn("anti-loop", manifest["keywords"])
        self.assertIn("@Click", manifest["description"])
        self.assertIn("structured", manifest["description"].lower())
        self.assertIn("revision-bound evidence", manifest["description"].lower())
        self.assertEqual(
            manifest["interface"]["shortDescription"],
            "Bind AI execution to approved intent and return verifiable evidence.",
        )
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)
        for prompt in manifest["interface"]["defaultPrompt"]:
            self.assertLessEqual(len(prompt), 128)

    def test_readmes_use_plugin_mention_as_the_default_invocation(self) -> None:
        for readme_name in README_NAMES:
            with self.subTest(readme=readme_name):
                readme = (ROOT / readme_name).read_text(encoding="utf-8")
                self.assertIn("@Click", readme)
                self.assertNotRegex(readme, r"(?m)^\$click\s")
                self.assertNotIn("<br/>$click", readme)

    def test_readmes_document_qualitative_profiles_and_exact_receipts(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for readme in (english, korean, chinese):
            self.assertIn("click-gate verify", readme)
            self.assertIn("click-gate receipt export", readme)
            self.assertIn("click-gate receipt verify", readme)
            self.assertIn("unsigned-integrity-only", readme)
        self.assertIn("Advisory verification profiles", english)
        self.assertIn("Advisory 검증 profile", korean)
        self.assertIn("Advisory 验证 profile", chinese)
        self.assertIn("qualitative statement", english)
        self.assertIn("정성적 표현", korean)
        self.assertIn("定性表达", chinese)
        for readme in (english, korean, chinese):
            self.assertIn("argv", readme)

    def test_evidence_economy_uses_structured_primary_source_references(self) -> None:
        click_skill = (ROOT / "skills" / "click" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        fix_skill = (ROOT / "skills" / "fix" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        verification = (
            ROOT
            / "skills"
            / "click"
            / "references"
            / "verification-profiles.md"
        ).read_text(encoding="utf-8")
        anti_loop = (
            ROOT / "skills" / "click" / "references" / "anti-loop-policy.md"
        ).read_text(encoding="utf-8")
        directive = (
            ROOT / "skills" / "click" / "references" / "directive-format.md"
        ).read_text(encoding="utf-8")
        grader = (ROOT / "evals" / "SEMANTIC_GRADER.md").read_text(
            encoding="utf-8"
        )

        for document in (verification, anti_loop, directive, grader):
            with self.subTest(document=document[:40]):
                self.assertIn("primary_evidence", document)
                self.assertIn("evidence", document)

        guarded = (
            ROOT / "skills" / "click" / "references" / "guarded-mode.md"
        ).read_text(encoding="utf-8")
        # The main Click Skill stays Evidence-only; the Guarded contract flow
        # and its references live in guarded-mode.md, which the Skill links.
        self.assertIn("references/guarded-mode.md", click_skill)
        self.assertIn("capability-protocol.md", click_skill)
        self.assertIn("anti-loop-policy.md", click_skill)
        self.assertIn("click-gate verify", click_skill)
        self.assertIn("click-gate status", click_skill)
        self.assertNotIn("click-gate stage", click_skill)
        self.assertNotIn("click-gate pass", click_skill)
        for skill in (guarded, fix_skill):
            with self.subTest(skill=skill[:40]):
                self.assertIn("verification-profiles.md", skill)
                self.assertIn("directive-format.md", skill)
                self.assertIn("anti-loop-policy.md", skill)
                self.assertIn("capability-protocol.md", skill)
                self.assertIn("CLICK_CONTRACT_ID", skill)
                self.assertIn("contract_id", skill)
        for skill in (click_skill, guarded, fix_skill):
            with self.subTest(skill=skill[:40]):
                self.assertNotIn("Python `-c`", skill)
                self.assertNotIn("three serial calls", skill)

        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for readme in (english, korean, chinese):
            self.assertIn('"evidence": [', readme)
            self.assertIn('"primary_evidence":', readme)
            self.assertIn('"kind": "argv"', readme)
            self.assertIn('"evidence_id":', readme)
            self.assertIn('"version":2', readme)
            self.assertIn("click-gate evidence", readme)

        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        evidence_runtime = (ROOT / "hooks" / "click_evidence.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("BROWSER_SOURCE_MARKERS", hook)
        self.assertNotIn("BROWSER_SOURCE_TERMS", hook)
        self.assertIn('source.get("kind") == "browser"', evidence_runtime)

    def test_plain_language_stays_digest_bound_and_is_rendered_once(self) -> None:
        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        contract_runtime = (ROOT / "hooks" / "click_contract.py").read_text(
            encoding="utf-8"
        )
        documents = (
            (
                ROOT / "skills" / "click" / "references" / "guarded-mode.md"
            ).read_text(encoding="utf-8"),
            (ROOT / "skills" / "fix" / "SKILL.md").read_text(encoding="utf-8"),
            (
                ROOT
                / "skills"
                / "click"
                / "references"
                / "translation-guide.md"
            ).read_text(encoding="utf-8"),
            (
                ROOT / "skills" / "click" / "references" / "directive-format.md"
            ).read_text(encoding="utf-8"),
            (ROOT / "evals" / "SEMANTIC_GRADER.md").read_text(encoding="utf-8"),
            (ROOT / "evals" / "golden-prompts.yaml").read_text(encoding="utf-8"),
        )

        self.assertIn(
            'STRING_FIELDS = ("outcome", "plain_language")', contract_runtime
        )
        self.assertIn("STRING_FIELDS = click_contract.STRING_FIELDS", hook)
        for document in documents:
            with self.subTest(document=document[:40]):
                self.assertIn("digest-bound", document)
                self.assertIn("plain_language", document)
                self.assertIn("once", document)

        grader = documents[-2]
        self.assertIn("duplicate rendering as a missed invariant", grader)

        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("one plain-language contract", english)
        self.assertIn("original canonical JSON stays hidden", english)
        self.assertIn("Approve, request changes, cancel, and view original", english)
        self.assertIn("위 계약문은 쉽게 풀어 설명한 것입니다", korean)
        self.assertIn("승인, 수정 요청, 취소, 원문 보기", korean)
        self.assertIn("通俗易懂的合同", chinese)
        self.assertIn("批准、要求修改、取消或查看原文", chinese)

    def test_readmes_document_distinct_turn_approval_and_git_mutation_guard(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for readme in (english, korean, chinese):
            self.assertIn("UserPromptSubmit", readme)
            self.assertIn("staged_turn_id", readme)
            self.assertIn("approved_turn_id", readme)
            self.assertIn("contract_id", readme)
            self.assertIn("CLICK_CONTRACT_ID=ctr_", readme)
            self.assertIn("non-ignored untracked", readme)
        self.assertIn("later user turn", english.lower())
        self.assertIn("다음 사용자 turn", korean)
        self.assertIn("后续用户 turn", chinese)

    def test_readmes_document_observable_anti_loop_guards_and_limits(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for readme in (english, korean, chinese):
            self.assertIn("48,000", readme)
            self.assertIn("update_plan", readme)
            self.assertIn("rg --files", readme)
        self.assertIn("non-blocking guidance", english)
        self.assertIn("비차단 안내", korean)
        self.assertIn("非阻断提示", chinese)
        self.assertIn("distinct-digest broad inventory remains available", english)
        self.assertIn("서로 다른 digest의 broad inventory", korean)
        self.assertIn("不同 digest 的 broad inventory", chinese)
        self.assertIn("Implementation without loops", english)
        self.assertIn("hidden reasoning", english)
        self.assertIn("실행 루프 없이 구현", korean)
        self.assertIn("숨은 추론", korean)
        self.assertIn("无循环实现", chinese)
        self.assertIn("隐藏推理", chinese)

    def test_readmes_document_structured_capabilities_and_shell_boundary(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        protocol = (
            ROOT
            / "skills"
            / "click"
            / "references"
            / "capability-protocol.md"
        ).read_text(encoding="utf-8")
        for document in (english, korean, chinese, protocol):
            self.assertIn("click-gate inspect", document)
            self.assertIn("click-gate mutate", document)
            self.assertIn("click-gate service", document)
            self.assertIn('"version":1', document)
            self.assertIn("shell=False", document)
            self.assertIn("process group", document)
            self.assertIn("process-control", document)
            self.assertIn("pkill", document)
        self.assertIn('"checks"', english)
        self.assertIn('"checks"', korean)
        self.assertIn('"checks"', chinese)
        self.assertIn("hosted tools", protocol)

    def test_marketplace_exposes_click_from_the_click_catalog(self) -> None:
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "click")
        self.assertEqual(marketplace["plugins"][0]["name"], "click")
        self.assertEqual(
            marketplace["plugins"][0]["source"]["ref"], "v0.24.5"
        )

    def test_ci_enforces_distribution_compilation_and_diff_validation(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python scripts/validate_distribution.py", workflow)
        self.assertIn("python -m compileall", workflow)
        self.assertIn("git diff --check", workflow)
        self.assertIn("push:\n    branches: [main]", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertTrue((ROOT / "scripts" / "validate_distribution.py").is_file())

    def test_release_documents_identify_current_and_preserve_release_history(self) -> None:
        for readme_name in README_NAMES:
            with self.subTest(readme=readme_name):
                readme = (ROOT / readme_name).read_text(encoding="utf-8")
                self.assertIn("v0.24.5", readme)
                self.assertIn("v0.24.4", readme)
                self.assertIn("v0.24.3", readme)
                self.assertIn("v0.24.1", readme)
                self.assertIn("v0.24.0", readme)
                self.assertIn("v0.23.0", readme)
                self.assertIn("v0.21.0", readme)
        notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("## v0.31.0", notes)
        self.assertIn("## v0.30.0", notes)
        self.assertIn("## v0.24.5", notes)
        self.assertIn("## v0.24.4", notes)
        self.assertIn("## v0.24.3", notes)
        self.assertIn("## v0.24.1", notes)
        self.assertIn("## v0.24.0", notes)
        self.assertIn("## v0.23.0", notes)
        self.assertIn("## v0.22.0", notes)
        self.assertIn("## v0.21.1", notes)
        self.assertIn("## v0.21.0", notes)
        self.assertIn("## v0.20.0", notes)
        self.assertNotIn("Unreleased v0.24", notes)
        golden = (ROOT / "evals" / "golden-prompts.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("version: 24", golden)
        self.assertIn(
            "## Evidence-bound completion in v0.21.0",
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "## v0.21.0의 증거별 완료 판정",
            (ROOT / "README.ko.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "## v0.21.0 的逐证据完成判定",
            (ROOT / "README.zh-CN.md").read_text(encoding="utf-8"),
        )

    def test_readmes_document_trusted_reads_and_pre_execution_claims(self) -> None:
        readmes = {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in README_NAMES
        }
        protocol = (
            ROOT / "skills" / "click" / "references" / "capability-protocol.md"
        ).read_text(encoding="utf-8")
        for name, readme in readmes.items():
            with self.subTest(readme=name):
                for marker in (
                    "gate-state",
                    "PLUGIN_DATA",
                    "LD_*",
                    "DYLD_*",
                    "GCONV_PATH",
                    "LOCPATH",
                    "one-use",
                    "snapshot",
                ):
                    self.assertIn(marker, readme)
        self.assertIn("Windows drive-prefixed forms", protocol)
        self.assertIn("nearest containing Git repository", protocol)
        self.assertIn("executes no mutation command", protocol)
        self.assertIn("initial protected snapshot", protocol)
        self.assertIn("concurrent same-user replacement", protocol)

    def test_completion_docs_match_per_source_and_service_state(self) -> None:
        modes = (
            ROOT / "skills" / "click" / "references" / "modes.md"
        ).read_text(encoding="utf-8")
        profiles = (
            ROOT
            / "skills"
            / "click"
            / "references"
            / "verification-profiles.md"
        ).read_text(encoding="utf-8")
        self.assertIn("every declared evidence source", modes)
        self.assertIn("no managed service remains active", modes)
        self.assertIn("no argv source", modes)
        self.assertIn("Typical argv evidence, when declared", profiles)
        self.assertIn("no argv source", profiles)

        localized_requirements = {
            "README.md": "no managed service remains active",
            "README.ko.md": "관리 서비스가 활성 상태가 아니",
            "README.zh-CN.md": "没有受管服务仍处于活动状态",
        }
        for name, phrase in localized_requirements.items():
            with self.subTest(readme=name):
                self.assertIn(
                    phrase, (ROOT / name).read_text(encoding="utf-8")
                )

    def test_readmes_explain_the_core_purpose_and_v021_update(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for readme in (english, korean, chinese):
            self.assertIn("codex plugin marketplace upgrade click", readme)
            self.assertIn("codex plugin add click@click", readme)
        self.assertIn("## Core purpose", english)
        self.assertIn("## 핵심 목적", korean)
        self.assertIn("## 核心目的", chinese)

    def test_product_constitution_is_canonical_and_classifies_guards(self) -> None:
        constitution = (ROOT / "PRODUCT_CONSTITUTION.md").read_text(
            encoding="utf-8"
        )
        classification = (ROOT / "GUARD_CLASSIFICATION.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Status: **Canonical**", constitution)
        self.assertIn(
            "Click returns revision-aware evidence for host-authorized AI execution",
            constitution,
        )
        self.assertIn("Evidence authority", constitution)
        self.assertIn("Guarded authority", constitution)
        self.assertIn("execution_authority: host", constitution)
        self.assertIn("approval_bound: false", constitution)
        self.assertIn("## Core admission test", constitution)
        self.assertIn("if the model were perfect", constitution)
        self.assertIn("runtime observe the relevant action or result", constitution)
        self.assertIn(
            "authority, side-effect control, or evidence integrity", constitution
        )
        for tier in ("### CORE", "### USER_POLICY", "### HEURISTIC"):
            self.assertIn(tier, constitution)
        self.assertIn("Cross-model, cross-host invariants", constitution)
        self.assertIn("model-specific workflow tuning", constitution)
        self.assertIn("argv verification receipts", constitution)
        self.assertIn("append-only capability-claim ledger", constitution)
        self.assertIn("does not yet provide a signed", constitution)

        self.assertIn(
            "v0.30 policy migrations complete; dependency-aware receipt reuse",
            classification,
        )
        self.assertIn("Evidence/Guarded dual authority completed for v0.35.0", classification)
        self.assertIn("approval projection completed for v0.36.0", classification)
        self.assertIn("`on` maps to Guarded and `manual` maps to Off", classification)
        self.assertIn("does not prove that its meaning was inside", classification)
        for tier in ("## CORE", "## USER_POLICY", "## HEURISTIC"):
            self.assertIn(tier, classification)
        self.assertIn("## Known assurance gaps", classification)
        self.assertIn("Legacy class\n  normalization is compatibility behavior", classification)
        self.assertIn("do not produce runtime advice", classification)
        self.assertIn("## Operational limits requiring disposition", classification)
        self.assertIn("## Migration order", classification)
        self.assertIn("Complete in v0.30.0", classification)
        self.assertIn("fresh, separately authorized retries", classification)
        self.assertIn("verification-time mutation", classification)
        self.assertIn("hooks/click_verification_policy.py", classification)
        self.assertIn("hooks/click_verification_meter.py", classification)
        self.assertIn("hooks/click_browser_advisory.py", classification)
        self.assertIn("hooks/click_dependency_cache.py", classification)
        self.assertIn("hooks/click_dependency_trace.py", classification)
        self.assertIn("hooks/click_observer_backend.py", classification)
        self.assertIn("hooks/click_observer_common.py", classification)
        self.assertIn("hooks/click_observer_linux.py", classification)
        self.assertIn("hooks/click_observer_macos.py", classification)
        self.assertIn("hooks/click_evidence_shards.py", classification)
        self.assertIn("hooks/click_incremental.py", classification)
        self.assertIn("hooks/click_shadow_intelligence.py", classification)
        self.assertIn("hooks/click_shadow_dashboard.py", classification)
        self.assertIn("hooks/click_host_coverage.py", classification)

        gate_runtime = (ROOT / "hooks" / "click_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(
            gate_runtime,
            r"\bmodel(?:_name|_id)?\b",
            "Click Core must not branch on model identity",
        )

    def test_click_supports_always_on_while_fix_remains_explicit(self) -> None:
        for skill_name in ("click", "fix"):
            with self.subTest(skill=skill_name):
                skill_root = ROOT / "skills" / skill_name
                skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
                metadata = (skill_root / "agents" / "openai.yaml").read_text(
                    encoding="utf-8"
                )
                self.assertIn(f"name: {skill_name}", skill_text)
                self.assertIn(f"${skill_name}", metadata)
                self.assertNotIn("[TODO:", skill_text)
        click_metadata = (
            ROOT / "skills" / "click" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        fix_metadata = (
            ROOT / "skills" / "fix" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: true", click_metadata)
        self.assertIn("allow_implicit_invocation: false", fix_metadata)

    def test_contract_id_is_the_canonical_approval_handoff(self) -> None:
        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        lifecycle = (ROOT / "hooks" / "click_lifecycle.py").read_text(
            encoding="utf-8"
        )
        directive = (
            ROOT / "skills" / "click" / "references" / "directive-format.md"
        ).read_text(encoding="utf-8")
        grader = (ROOT / "evals" / "SEMANTIC_GRADER.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("CONTRACT_ID_PATTERN", hook)
        self.assertIn("secrets.token_hex(16)", lifecycle)
        self.assertIn('"contract_id": contract_id', lifecycle)
        self.assertIn("not the Execution ", lifecycle)
        self.assertIn("Contract JSON", lifecycle)
        self.assertNotIn("pass '<Execution Contract JSON>'", hook)

        for document in (directive, grader):
            with self.subTest(document=document[:40]):
                self.assertIn("contract_id", document)
                self.assertIn("later user turn", document)
                self.assertIn("JSON", document)
        self.assertIn("CLICK_CONTRACT_ID=ctr_", directive)
        self.assertIn("click-gate pass ctr_<32hex>", directive)
        self.assertIn("never resend", directive)

    def test_persistent_modes_and_read_only_review_are_documented(self) -> None:
        modes = (
            ROOT / "skills" / "click" / "references" / "modes.md"
        ).read_text(encoding="utf-8")
        hook_config = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        for phrase in (
            "click-gate default evidence",
            "click-gate default guarded",
            "click-gate default off",
            "click-gate review",
            "click-gate bypass",
        ):
            self.assertIn(phrase, modes)
        self.assertIn("UserPromptSubmit", hook_config["hooks"])

    def test_runtime_hook_has_no_external_python_dependency(self) -> None:
        sources = {
            name: (ROOT / "hooks" / name).read_text(encoding="utf-8")
            for name in (
                "click_browser.py",
                "click_browser_advisory.py",
                "click_capability.py",
                "click_claims.py",
                "click_contract.py",
                "click_contract_state.py",
                "click_dependency_cache.py",
                "click_dependency_trace.py",
                "click_observer_backend.py",
                "click_observer_common.py",
                "click_observer_linux.py",
                "click_observer_macos.py",
                "click_evidence_shards.py",
                "click_incremental.py",
                "click_shadow_dashboard.py",
                "click_shadow_intelligence.py",
                "click_evidence.py",
                "click_receipt.py",
                "click_receipt_runtime.py",
                "click_runner_transport.py",
                "click_runtime_state.py",
                "click_host_coverage.py",
                "click_host_router.py",
                "click_import_bootstrap.py",
                "click_inspection.py",
                "click_inspection_policy.py",
                "click_lifecycle.py",
                "click_mode.py",
                "click_gate.py",
                "click_mutation.py",
                "click_observation.py",
                "click_process.py",
                "click_prompt.py",
                "click_service.py",
                "click_state.py",
                "click_verification.py",
                "click_verification_meter.py",
                "click_verification_policy.py",
            )
        }
        for name, source in sources.items():
            for forbidden in ("requests", "yaml", "pydantic", "openai"):
                with self.subTest(name=name, forbidden=forbidden):
                    self.assertNotIn(f"import {forbidden}", source)
                    self.assertNotIn(f"from {forbidden}", source)

    def test_process_mechanics_are_isolated_from_gate_policy(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        process = (ROOT / "hooks" / "click_process.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("subprocess.run(", gate)
        self.assertNotIn("subprocess.Popen(", gate)
        for forbidden in ("click_gate", "click_state", "evidence"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", process)
                self.assertNotIn(f"from {forbidden}", process)

    def test_managed_service_lifecycle_is_isolated_from_gate_policy(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        service = (ROOT / "hooks" / "click_service.py").read_text(
            encoding="utf-8"
        )
        service_imports = _imported_module_names(service)

        self.assertIn("click_service", gate)
        for required in (
            "def prepare_service(",
            "def claim_service_runner(",
            "def run_service_supervisor(",
            "def run_service_start(",
            "def run_service_stop(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, service)
        for forbidden in (
            "click_browser_advisory",
            "click_contract",
            "click_dependency_cache",
            "click_evidence",
            "click_gate",
            "click_host_coverage",
            "click_verification_meter",
            "click_verification_policy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in service_imports),
                    service_imports,
                )

    def test_mutation_runtime_is_isolated_from_gate_and_sibling_domains(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        mutation = (ROOT / "hooks" / "click_mutation.py").read_text(
            encoding="utf-8"
        )
        mutation_imports = _imported_module_names(mutation)

        self.assertIn("click_mutation", gate)
        for required in (
            "def mark_contract_mutated(",
            "def prepare(",
            "def claim_run(",
            "def record_result(",
            "def record_boundary(",
            "def run(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, mutation)
        for forbidden in (
            "click_browser",
            "click_contract",
            "click_gate",
            "click_host_coverage",
            "click_process",
            "click_service",
            "click_verification_meter",
            "click_verification_policy",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in mutation_imports),
                    mutation_imports,
                )

    def test_evidence_ledger_is_isolated_from_gate_state_and_process(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        evidence = (ROOT / "hooks" / "click_evidence.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("click_evidence", gate)
        for forbidden in ("click_gate", "click_state", "click_process"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", evidence)
                self.assertNotIn(f"from {forbidden}", evidence)

    def test_completion_receipt_has_a_pure_body_and_separate_runtime(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        lifecycle = (ROOT / "hooks" / "click_lifecycle.py").read_text(
            encoding="utf-8"
        )
        receipt = (ROOT / "hooks" / "click_receipt.py").read_text(
            encoding="utf-8"
        )
        receipt_runtime = (ROOT / "hooks" / "click_receipt_runtime.py").read_text(
            encoding="utf-8"
        )
        claims = (ROOT / "hooks" / "click_claims.py").read_text(encoding="utf-8")
        builder = (
            ROOT / "scripts" / "build_antigravity_distribution.py"
        ).read_text(encoding="utf-8")

        for required in (
            "RECEIPT_VERSION = 2",
            "SHARD_RECEIPT_VERSION = 3",
            "def validate_receipt(",
            "def canonical_bytes(",
            "def receipt_digest(",
            "def create_envelope(",
            "def validate_envelope(",
            "known-surfaces-only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, receipt)
        for forbidden in (
            "import click_",
            "from .",
            "from click_",
            "click_state",
            "runner_token_digest",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, receipt)
        self.assertIn("click_receipt_runtime", gate)
        self.assertNotIn("import click_receipt\n", gate)
        self.assertNotIn("click_receipt", lifecycle)
        self.assertIn('"click_receipt.py"', builder)
        self.assertIn('"click_receipt_runtime.py"', builder)
        self.assertIn('"click_claims.py"', builder)
        for forbidden in ("click_gate", "click_lifecycle", "click_state", "click_process"):
            with self.subTest(runtime_forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", receipt_runtime)
                self.assertNotIn(f"from {forbidden}", receipt_runtime)
        self.assertIn("click_receipt", receipt_runtime)
        self.assertNotIn("import click_", claims)

    def test_lifecycle_orchestration_is_isolated_from_host_routing(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        lifecycle = (ROOT / "hooks" / "click_lifecycle.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("click_lifecycle", gate)
        for required in (
            "def prompt_context(",
            "def stage_contract(",
            "def pass_contract(",
            "def record_evidence_completion(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, lifecycle)
        for forbidden in (
            "click_gate",
            "click_host_coverage",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", lifecycle)
                self.assertNotIn(f"from {forbidden}", lifecycle)

    def test_verification_policy_and_meter_are_separate_runtime_boundaries(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        verification = (ROOT / "hooks" / "click_verification.py").read_text(
            encoding="utf-8"
        )
        contract = (ROOT / "hooks" / "click_contract.py").read_text(
            encoding="utf-8"
        )
        policy = (ROOT / "hooks" / "click_verification_policy.py").read_text(
            encoding="utf-8"
        )
        meter = (ROOT / "hooks" / "click_verification_meter.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("click_verification_policy", gate)
        self.assertNotIn("click_verification_meter", gate)
        self.assertIn("click_verification_policy", verification)
        self.assertIn("click_verification_plan", verification)
        planning = (ROOT / "hooks" / "click_verification_plan.py").read_text(encoding="utf-8")
        self.assertIn("click_verification_meter", planning)
        self.assertIn("click_verification_policy", contract)
        self.assertIn("click_verification_meter", contract)
        self.assertIn("legacy numeric table", policy)
        self.assertIn("not use those numbers as authority", policy)
        self.assertIn("state and direct-caller compatibility", meter)
        self.assertIn("not runtime advice", meter)
        for source in (policy, meter):
            for forbidden in (
                "import click_contract",
                "import click_evidence",
                "import click_gate",
                "import click_process",
                "import click_state",
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_browser_advisory_is_separate_from_receipt_integrity(self) -> None:
        gate = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        browser = (ROOT / "hooks" / "click_browser.py").read_text(
            encoding="utf-8"
        )
        browser_imports = _imported_module_names(browser)
        advisory = (ROOT / "hooks" / "click_browser_advisory.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("click_browser", gate)
        self.assertIn("click_browser_advisory", gate)
        for required in (
            "def prepare(",
            "def record_result(",
            "def finalize_evidence(",
        ):
            with self.subTest(required=required):
                self.assertIn(required, browser)
        self.assertIn("Workflow suggestions remain", browser)
        self.assertNotIn("permissionDecision", browser)
        self.assertIn("does not grant or deny Browser authority", advisory)
        self.assertNotIn("permissionDecision", advisory)
        self.assertNotIn("tool_use_id", advisory.split("def repeat_advisory", 1)[0])
        for forbidden in (
            "import click_contract",
            "import click_evidence",
            "import click_gate",
            "import click_process",
            "import click_state",
            "import platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, advisory)
        for forbidden in (
            "click_contract",
            "click_gate",
            "click_process",
            "click_service",
            "click_verification_meter",
            "click_verification_policy",
            "platform_protocol",
        ):
            with self.subTest(browser_forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name.split(".") for name in browser_imports),
                    browser_imports,
                )

    def test_compact_contract_replaces_the_verbose_execution_fields(self) -> None:
        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        directive = (
            ROOT / "skills" / "click" / "references" / "directive-format.md"
        ).read_text(encoding="utf-8")
        for required in (
            "outcome",
            "boundary",
            "must_hold",
            "build",
            "verification",
            "plain_language",
        ):
            with self.subTest(required=required):
                self.assertIn(required, hook)
                self.assertIn(required, directive)
        for removed in (
            '"invariants"',
            '"system_semantics"',
            '"implementation"',
            '"phases"',
            '"steps"',
            '"tasks"',
            '"plan"',
            '"execution_order"',
            '"minimality"',
            '"proof"',
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, hook)

    def test_public_tree_contains_no_ballast_state(self) -> None:
        forbidden_parts = {
            "ballast",
            "checkpoint.md",
            "decisions.md",
            "handoff.md",
            "product-truth.md",
        }
        tracked_candidates = [
            path.relative_to(ROOT)
            for path in ROOT.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]
        for path in tracked_candidates:
            lowered = {part.lower() for part in path.parts}
            with self.subTest(path=path):
                self.assertTrue(lowered.isdisjoint(forbidden_parts))

    def test_golden_cases_cover_evidence_guarded_and_review_routing(self) -> None:
        catalog = (
            ROOT / "evals" / "golden-prompts.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("version: 24", catalog)
        for case_id in (
            "unset-evidence-default",
            "evidence-trivial-edit",
            "evidence-code-review",
            "evidence-explanation",
        ):
            self.assertIn(f"id: {case_id}", catalog)
        self.assertIn("default_mode: evidence", catalog)
        self.assertIn("default_mode: guarded", catalog)
        self.assertIn("id: structured-capability-active-build", catalog)
        self.assertIn("id: structured-capability-review", catalog)
        self.assertIn("id: completed-contract-rollover", catalog)
        self.assertIn("id: verification-minimum-class", catalog)
        self.assertIn("id: broad-suite-evidence-shards", catalog)
        self.assertIn("id: verification-workspace-mutation", catalog)
        self.assertIn("id: distinct-turn-contract-approval", catalog)
        self.assertIn("id: active-lifecycle-plan-advisory", catalog)
        self.assertIn("id: broad-inventory-advisory", catalog)
        self.assertIn("id: logical-repeat-advisory", catalog)
        self.assertIn("id: verification-policy-boundary", catalog)
        self.assertIn("id: cheapest-evidence-browser-game", catalog)

    def test_multiplatform_adapter_is_documented_without_false_parity(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for value, heading, limitation in (
            (english, "Google Antigravity adapter", "not currently supported"),
            (korean, "Google Antigravity 어댑터", "아직 지원하지 않습니다"),
            (chinese, "Google Antigravity 适配器", "目前还不支持"),
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, value)
                self.assertIn("dist/antigravity", value)
                self.assertIn(limitation, value)
        platform = (
            ROOT / "platforms" / "antigravity" / "README.md"
        ).read_text(encoding="utf-8")
        self.assertIn("model_stop", platform)
        self.assertIn("cannot rewrite tool arguments", platform)
        self.assertIn("not claimed", platform)
        self.assertIn("control inspect", platform)
        self.assertIn("non-blocking narrowing advisory", platform)


if __name__ == "__main__":
    unittest.main()
