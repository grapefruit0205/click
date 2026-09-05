from __future__ import annotations

import json
import unittest

try:
    from . import repository_policy_core as core
except ImportError:  # unittest discovery with tests/ as the import root.
    import repository_policy_core as core


ROOT = core.ROOT
README_NAMES = core.README_NAMES
TECHNICAL_LINKS = (
    "PRODUCT_CONSTITUTION.md",
    "GUARD_CLASSIFICATION.md",
    "skills/click/references/modes.md",
    "skills/click/references/directive-format.md",
    "skills/click/references/verification-profiles.md",
    "skills/click/references/capability-protocol.md",
    "skills/click/references/observer-v1.md",
    "skills/click/references/shadow-intelligence-v1.md",
    "skills/click/references/evidence-shards-v1.md",
    "skills/click/references/anti-loop-policy.md",
)


def _readmes() -> dict[str, str]:
    return {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in README_NAMES
    }


def _reference(name: str) -> str:
    return (
        ROOT / "skills" / "click" / "references" / name
    ).read_text(encoding="utf-8")


class RepositoryPolicyTests(core.RepositoryPolicyTests):
    """Keep runtime policy strict without freezing README marketing prose."""

    def test_manifest_declares_click_one_shot_release(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "click")
        self.assertEqual(manifest["version"], "0.81.1")
        self.assertEqual(manifest["license"], "MIT")
        combined_copy = " ".join(
            (
                manifest["description"],
                manifest["interface"]["shortDescription"],
                manifest["interface"]["longDescription"],
            )
        ).lower()
        for concept in ("evidence", "guarded", "approval"):
            self.assertIn(concept, combined_copy)
        self.assertIn("anti-loop", manifest["keywords"])
        self.assertLessEqual(len(manifest["interface"]["shortDescription"]), 100)
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)
        for prompt in manifest["interface"]["defaultPrompt"]:
            self.assertLessEqual(len(prompt), 128)

    def test_marketplace_exposes_click_from_the_click_catalog(self) -> None:
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "click")
        self.assertEqual(marketplace["plugins"][0]["name"], "click")
        self.assertEqual(
            marketplace["plugins"][0]["source"]["ref"], "v0.81.1"
        )

    def test_readmes_lead_with_incremental_verification_positioning(self) -> None:
        for name, readme in _readmes().items():
            with self.subTest(readme=name):
                opening = "\n".join(readme.splitlines()[:45]).lower()
                self.assertIn("incremental verification", opening)
                self.assertIn("revision-aware evidence", opening)
                self.assertIn("evidence", readme)
                self.assertIn("guarded", readme.lower())
                self.assertIn("off", readme.lower())
                self.assertLessEqual(len(readme.splitlines()), 240)
                self.assertLessEqual(len(readme), 16_000)
                for link in TECHNICAL_LINKS:
                    self.assertIn(link, readme)
                for internal_copy in (
                    "staged_turn_id",
                    "approved_turn_id",
                    "runner_token_digest",
                    "GCONV_PATH",
                    '"primary_evidence":',
                    '"evidence_id":',
                ):
                    self.assertNotIn(internal_copy, readme)
        english = _readmes()["README.md"]
        self.assertIn(
            "Incremental verification for coding agents.", english
        )
        self.assertIn(
            "Click keeps passing checks reusable until the code they depend on "
            "actually changes.",
            english,
        )
        self.assertIn(
            "Click does not prove that the code is correct or that the selected "
            "tests are sufficient.",
            english,
        )
        for readme in _readmes().values():
            self.assertIn("click-gate observer off", readme)
            self.assertIn("benchmarks/incremental_verification.py", readme)

    def test_readmes_document_qualitative_profiles_and_exact_receipts(self) -> None:
        for readme in _readmes().values():
            self.assertIn("click-gate receipt export", readme)
            self.assertIn("click-gate receipt verify", readme)
            self.assertIn("unsigned-integrity-only", readme)
            self.assertIn("verification-profiles.md", readme)

        profiles = _reference("verification-profiles.md")
        for marker in (
            "qualitative profile",
            "compatibility",
            "custom program",
            "wrapper",
            "click-gate verify",
            "evidence_id",
        ):
            self.assertIn(marker.lower(), profiles.lower())

    def test_evidence_economy_uses_structured_primary_source_references(self) -> None:
        click_skill = (ROOT / "skills" / "click" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        fix_skill = (ROOT / "skills" / "fix" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        documents = (
            _reference("verification-profiles.md"),
            _reference("anti-loop-policy.md"),
            _reference("directive-format.md"),
            (ROOT / "evals" / "SEMANTIC_GRADER.md").read_text(encoding="utf-8"),
        )
        for document in documents:
            self.assertIn("primary_evidence", document)
            self.assertIn("evidence", document)
        for skill in (click_skill, fix_skill):
            for reference in (
                "verification-profiles.md",
                "directive-format.md",
                "anti-loop-policy.md",
                "capability-protocol.md",
            ):
                self.assertIn(reference, skill)

        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        evidence_runtime = (
            ROOT / "hooks" / "click_evidence.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("BROWSER_SOURCE_MARKERS", hook)
        self.assertNotIn("BROWSER_SOURCE_TERMS", hook)
        self.assertIn('source.get("kind") == "browser"', evidence_runtime)

    def test_plain_language_stays_digest_bound_and_is_rendered_once(self) -> None:
        hook = (ROOT / "hooks" / "click_gate.py").read_text(encoding="utf-8")
        contract_runtime = (
            ROOT / "hooks" / "click_contract.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'STRING_FIELDS = ("outcome", "plain_language")', contract_runtime
        )
        self.assertIn("STRING_FIELDS = click_contract.STRING_FIELDS", hook)

        documents = (
            ROOT / "skills" / "click" / "SKILL.md",
            ROOT / "skills" / "fix" / "SKILL.md",
            ROOT / "skills" / "click" / "references" / "translation-guide.md",
            ROOT / "skills" / "click" / "references" / "directive-format.md",
            ROOT / "evals" / "SEMANTIC_GRADER.md",
            ROOT / "evals" / "golden-prompts.yaml",
        )
        for path in documents:
            text = path.read_text(encoding="utf-8")
            self.assertIn("digest-bound", text)
            self.assertIn("plain_language", text)
            self.assertIn("once", text)

    def test_readmes_document_distinct_turn_approval_and_git_mutation_guard(self) -> None:
        lifecycle = (ROOT / "hooks" / "click_lifecycle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("staged_turn_id", lifecycle)
        self.assertIn("approved_turn_id", lifecycle)
        hook_config = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        self.assertIn("UserPromptSubmit", hook_config["hooks"])
        directive = _reference("directive-format.md")
        self.assertIn("CLICK_CONTRACT_ID=ctr_", directive)
        self.assertIn("later user turn", directive)
        self.assertIn(
            "non-ignored untracked", _reference("capability-protocol.md")
        )

    def test_readmes_document_observable_anti_loop_guards_and_limits(self) -> None:
        anti_loop = _reference("anti-loop-policy.md")
        for marker in (
            "48,000",
            "update_plan",
            "hidden reasoning",
            "Prefer narrow follow-up after broad context",
            "fresh authorization",
            "Verification that changed protected repository content",
        ):
            self.assertIn(marker, anti_loop)

    def test_readmes_document_structured_capabilities_and_shell_boundary(self) -> None:
        protocol = _reference("capability-protocol.md")
        for marker in (
            "click-gate inspect",
            "click-gate mutate",
            "click-gate service",
            "click-gate dashboard",
            "click-gate verify",
            "shell=False",
            "process group",
            "process-control",
            "pkill",
        ):
            self.assertIn(marker, protocol)

    def test_shadow_observer_contract_is_non_authoritative_and_content_free(self) -> None:
        observer = _reference("observer-v1.md")
        observer_words = " ".join(observer.lower().split())
        for marker in (
            '"authoritative": false',
            '"reuse_authorized": false',
            "no file contents",
            "raw environment variables",
            "absolute external paths",
            "never feeds Click's authority-bearing dependency observation",
            "macOS Phase 3B.1 collector",
            "never invokes `sudo`",
            "Windows Phase 3B.2 collector",
            "never runs the target again",
            "logman.exe",
            "tracerpt.exe",
            "Unknown schema versions fail closed",
        ):
            self.assertIn(marker.lower(), observer_words)

        dependency_runtime = (
            ROOT / "hooks" / "click_dependency_cache.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SHADOW_OBSERVER_SCHEMA_VERSION = 1", dependency_runtime)
        self.assertIn('SHADOW_OBSERVER_MODE = "shadow"', dependency_runtime)
        trace_runtime = (
            ROOT / "hooks" / "click_dependency_trace.py"
        ).read_text(encoding="utf-8")
        self.assertIn("run_command", trace_runtime)
        self.assertIn("reuse_authorized", dependency_runtime)
        for path in (
            ROOT / "hooks" / "click_evidence.py",
            ROOT / "hooks" / "click_receipt.py",
            ROOT / "hooks" / "click_receipt_runtime.py",
        ):
            self.assertNotIn(
                "shadow_observer",
                path.read_text(encoding="utf-8"),
            )

    def test_shadow_intelligence_is_non_authoritative_local_telemetry(self) -> None:
        intelligence = _reference("shadow-intelligence-v1.md")
        normalized = " ".join(intelligence.lower().split())
        for marker in (
            '"authoritative": false',
            '"reuse_authorized": false',
            "every compatible argv check still runs exactly once",
            "dashboard exposes no `actual_saved_ms` claim",
            "estimated_avoided_ms",
            "shadow telemetry is nested separately",
            "127.0.0.1",
            "no state-changing method",
            "click-gate dashboard start",
            "current click lifecycle",
        ):
            self.assertIn(marker.lower(), normalized)

        runtime = (
            ROOT / "hooks" / "click_shadow_intelligence.py"
        ).read_text(encoding="utf-8")
        dashboard = (
            ROOT / "hooks" / "click_shadow_dashboard.py"
        ).read_text(encoding="utf-8")
        self.assertIn('actual_saved_ms": 0', runtime)
        self.assertIn('"reuse-candidate"', runtime)
        self.assertIn('("127.0.0.1", 0)', dashboard)
        self.assertIn("Content-Security-Policy", dashboard)
        self.assertNotIn("Access-Control-Allow-Origin", dashboard)
        for forbidden in (
            "click_receipt",
            "click_evidence",
            "runtime-dependency-observation-v1",
        ):
            self.assertNotIn(forbidden, runtime)

    def test_evidence_shards_are_decomposition_only_and_fail_closed(self) -> None:
        reference = _reference("evidence-shards-v1.md")
        normalized = " ".join(reference.lower().split())
        for marker in (
            ".click/evidence-shards.json",
            "authorizes decomposition only",
            "exactly one shard",
            "runs the original parent suite",
            "existing reuse rules",
            "receipt v3",
            "does not provide zero-configuration framework discovery",
        ):
            self.assertIn(marker.lower(), normalized)

        shards = (
            ROOT / "hooks" / "click_evidence_shards.py"
        ).read_text(encoding="utf-8")
        verification = (
            ROOT / "hooks" / "click_verification.py"
        ).read_text(encoding="utf-8")
        change_policy = (
            ROOT / "hooks" / "click_change_policy.py"
        ).read_text(encoding="utf-8")
        receipt = (ROOT / "hooks" / "click_receipt.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("running_plan_error", verification)
        self.assertIn("collapse_shard_plan", verification)
        self.assertIn('SHARD_RECEIPT_VERSION = 3', receipt)
        self.assertIn('".click/evidence-shards.json"', change_policy)
        for forbidden in (
            "click_gate",
            "click_contract",
            "click_evidence",
            "click_state",
            "click_process",
            "platform_protocol",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", shards)
                self.assertNotIn(f"from {forbidden}", shards)

    def test_release_documents_identify_current_and_preserve_release_history(self) -> None:
        for readme in _readmes().values():
            self.assertIn("v0.81.1", readme)
            self.assertIn("codex plugin marketplace upgrade click", readme)
            self.assertIn("codex plugin add click@click", readme)
            self.assertIn("RELEASE_NOTES.md", readme)

        notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        for marker in (
            "## v0.81.1",
            "## v0.81.0",
            "## v0.80.0",
            "## v0.70.0",
            "## v0.60.0",
            "## v0.51.1",
            "## v0.51.0",
            "## v0.50.0",
            "## v0.36.2",
            "## v0.36.1",
            "## v0.36.0",
            "## v0.35.0",
            "## v0.33.0",
            "## v0.32.0",
            "## v0.31.0",
            "## v0.30.0",
            "## v0.24.6",
            "## v0.24.5",
            "## v0.24.4",
            "## v0.24.3",
            "## v0.24.1",
            "## v0.24.0",
            "## v0.23.0",
            "## v0.22.0",
            "## v0.21.1",
            "## v0.21.0",
            "## v0.20.0",
        ):
            self.assertIn(marker, notes)

    def test_release_automation_has_no_version_specific_one_off_workflow(self) -> None:
        workflows = ROOT / ".github" / "workflows"
        self.assertEqual(list(workflows.glob("publish-v*.yml")), [])

    def test_readmes_document_trusted_reads_and_pre_execution_claims(self) -> None:
        protocol = _reference("capability-protocol.md")
        for marker in (
            "gate-state",
            "LD_*",
            "DYLD_*",
            "GCONV_PATH",
            "LOCPATH",
            "one-use",
            "snapshot",
            "Windows drive-prefixed forms",
            "nearest containing Git repository",
            "executes no mutation command",
            "initial protected snapshot",
            "concurrent same-user replacement",
        ):
            self.assertIn(marker, protocol)

    def test_completion_docs_match_per_source_and_service_state(self) -> None:
        modes = _reference("modes.md")
        profiles = _reference("verification-profiles.md")
        for marker in (
            "every declared evidence source",
            "no managed service remains active",
            "no argv source",
        ):
            self.assertIn(marker, modes)
        self.assertIn("Typical argv evidence, when declared", profiles)
        self.assertIn("no argv source", profiles)

    def test_readmes_explain_the_core_purpose_and_v021_update(self) -> None:
        for readme in _readmes().values():
            self.assertIn("revision-aware evidence", readme.lower())
            self.assertIn("codex plugin marketplace upgrade click", readme)
            self.assertIn("codex plugin add click@click", readme)

    def test_multiplatform_adapter_is_documented_without_false_parity(self) -> None:
        for readme in _readmes().values():
            self.assertIn("dist/antigravity", readme)
            self.assertIn("platforms/antigravity/README.md", readme)

        platform = (
            ROOT / "platforms" / "antigravity" / "README.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "model_stop",
            "cannot rewrite tool arguments",
            "not claimed",
            "control inspect",
            "non-blocking narrowing advisory",
        ):
            self.assertIn(marker, platform)

    def test_dependency_aware_receipts_are_opt_in_and_documented(self) -> None:
        for readme in _readmes().values():
            self.assertIn(".click/evidence-dependencies.json", readme)

        directive = _reference("directive-format.md")
        protocol = _reference("capability-protocol.md")
        self.assertIn("Omit", directive)
        self.assertIn("dependencies", directive)
        for marker in (
            "dependency-aware cross-revision reuse",
            "relevant normalized entry",
            "PostToolUse",
            "Repository-internal relative symlinks",
        ):
            self.assertIn(marker, protocol)
        hook_config = json.loads(
            (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        post_matchers = [
            entry.get("matcher", "")
            for entry in hook_config["hooks"]["PostToolUse"]
        ]
        self.assertTrue(any("apply_patch" in matcher for matcher in post_matchers))


if __name__ == "__main__":
    unittest.main()
