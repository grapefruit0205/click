"""Reviewable unittest and pytest shard proposals; never policy authority."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import time

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(adapters, inventory, dependencies, shards, verification) = click_import_bootstrap.load_siblings(
    __package__, "click_verification_adapters",
    "click_test_inventory", "click_dependency_cache",
    "click_evidence_shards", "click_verification"
)

MAX_SHARDS = 64
MAX_CHILD_ARGUMENTS = 64
MAX_PROPOSAL_SECONDS = 120.0
POLICY_NAMES = ("evidence-shards.json", "evidence-dependencies.json", "evidence-reuse.json")


def child_command(parent: dict, filename: str) -> list[str]:
    """Delegate exact child selection to the statically registered adapter."""
    argv = adapters.split_child_command(parent, filename)
    if argv is None:
        raise inventory.AnalysisError("adapter-split-unsupported")
    return argv


def validate_command(argv: list[str], root: Path, cwd: Path) -> None:
    if len(argv) > MAX_CHILD_ARGUMENTS:
        raise inventory.AnalysisError("child-command-limit")
    inventory.parse_command(argv, root, cwd)
    value, _, error = verification.validate_batch(json.dumps({
        "version": 2, "checks": [{"evidence_id": "E_PROPOSAL", "argv": argv,
                                   "class": "broad"}]}), "focused")
    if error or value is None:
        raise inventory.AnalysisError("invalid-verification-command")


def equivalence(parent: dict, children: list[dict]) -> dict:
    adapter_id = str(parent.get("adapter", ""))
    expected = Counter(item["id"] for item in parent["inventory"])
    observed = Counter(item["id"] for child in children for item in child["inventory"])
    if observed != expected:
        raise inventory.AnalysisError("child-inventory-mismatch")
    # IDs alone do not bind source/import identity or whole-module ownership.
    records = {item["id"]: item for item in parent["inventory"]}
    owners: dict[str, int] = {}
    for index, child in enumerate(children):
        for item in child["inventory"]:
            if records.get(item["id"]) != item:
                raise inventory.AnalysisError("child-source-mismatch")
            owner = adapters.inventory_owner(adapter_id, item)
            if owner is None:
                raise inventory.AnalysisError("inventory-owner-unavailable")
            if owner in owners and owners[owner] != index:
                raise inventory.AnalysisError("module-split-or-duplicated")
            owners[owner] = index
    return {"parent_count": sum(expected.values()), "child_count": sum(observed.values()),
            "multiset_equal": True, "module_indivisible": True,
            "semantic_equivalence_proven": False}


def existing_configuration(root: Path) -> tuple[list[dict], list[dict]]:
    """Preserve owner declarations without mixing unrelated check scopes."""
    metadata, entries = [], []
    for name in POLICY_NAMES:
        target = root / ".click" / name
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or not target.is_file() or target.stat().st_size > shards.MAX_CONFIG_BYTES:
            raise inventory.AnalysisError("existing-policy-unreadable")
        content = target.read_bytes()
        metadata.append({"path": ".click/" + name, "content_digest": inventory.digest(content.hex()),
                         "action": "preserve-and-review"})
        if name != "evidence-dependencies.json":
            continue
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise inventory.AnalysisError("existing-dependencies-invalid") from exc
        if (not isinstance(value, dict) or set(value) != {"version", "entries"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["entries"], list) or not value["entries"]
                or len(value["entries"]) > 128):
            raise inventory.AnalysisError("existing-dependencies-invalid")
        seen = set()
        for entry in value["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"checks", "paths"}:
                raise inventory.AnalysisError("existing-dependencies-invalid")
            patterns, error = dependencies.normalize_patterns(entry["paths"])
            key = dependencies.manifest_group_digest(entry["checks"])
            if error or not key or key in seen:
                raise inventory.AnalysisError("existing-dependencies-invalid")
            seen.add(key)
            entries.append(entry)
    return metadata, entries


def propose(project: Path, argv: list[str] | None, *, cwd: Path | None = None,
            limits: inventory.Limits | None = None) -> dict:
    limits = limits or inventory.Limits()
    deadline = time.monotonic() + MAX_PROPOSAL_SECONDS

    def remaining_limits() -> inventory.Limits:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise inventory.AnalysisError("proposal-time-limit")
        return replace(limits, timeout=min(limits.timeout, remaining / 2))

    parent_started = time.perf_counter_ns()
    parent = inventory.analyze(project, argv, cwd=cwd, limits=remaining_limits())
    parent_analysis_ms = (time.perf_counter_ns() - parent_started) / 1_000_000
    result = {"version": 1, "kind": "test-shard-proposal", "adapter": parent.get("adapter", inventory.ADAPTER),
              "status": parent["status"], "reasons": list(parent["reasons"]),
              "candidate_only": True, "authority": False, "reuse_ready": False,
              "proposal_ready": False, "review_state": "pending", "analysis": parent,
              "timing_samples": {"parent_analysis_ms": parent_analysis_ms},
              "children": [], "proposals": {}}
    if parent["status"] != "analysis-complete":
        return result
    result.update(project_identity=parent["project_identity"],
                  inventory_digest=parent["inventory_digest"],
                  workspace_digest=parent["workspace_digest"],
                  generation_reason="Whole-module discovery with exact filename selectors; "
                  "shared dependency/configuration candidates retained.")
    root = Path(project).resolve()
    execution_cwd = (cwd or root).resolve()
    try:
        before = inventory.workspace_snapshot(root, limits)
        if inventory.digest(before) != parent["workspace_digest"]:
            raise inventory.AnalysisError("project-changed-during-proposal")
        existing, old_entries = existing_configuration(root)
        parent_key = dependencies.manifest_group_digest([parent["command"]["argv"]])
        inherited = {
            path for entry in old_entries
            if dependencies.manifest_group_digest(entry["checks"]) == parent_key
            for path in entry["paths"]
        }
        result["existing_configuration"] = existing
        candidates = parent["dependencies"]
        result["split_risks"] = candidates["split_risks"]
        result["unknown_dependencies"] = candidates["unknown"]
        if candidates["split_risks"] or candidates["unknown"]:
            raise inventory.AnalysisError("split-independence-needs-review")
        command = parent["command"]
        validate_command(command["argv"], root, execution_cwd)
        selected = adapters.discovery_files(
            parent, list(before), root=root, cwd=execution_cwd
        )
        if selected is None:
            raise inventory.AnalysisError("adapter-inventory-unsupported")
        discovered = adapters.discovered_files(parent)
        if discovered is None:
            raise inventory.AnalysisError("adapter-inventory-unsupported")
        if set(selected) - discovered:
            raise inventory.AnalysisError("undiscovered-file-matches-policy-inventory")
        groups: dict[str, list[str]] = {}
        for path in selected:
            # Pattern-based adapters cannot safely treat metacharacters as an
            # exact filename. Jest children use --runTestsByPath instead.
            if (
                parent.get("adapter") != adapters.JEST_ADAPTER
                and any(character in path for character in "*?[]")
            ):
                raise inventory.AnalysisError("unsupported-selector-path")
            key = adapters.split_group_key(str(parent.get("adapter", "")), path)
            if key is None:
                raise inventory.AnalysisError("adapter-split-unsupported")
            groups.setdefault(key, []).append(path)
        if len(groups) < 2:
            raise inventory.AnalysisError("no-useful-module-split")
        if len(groups) > MAX_SHARDS:
            raise inventory.AnalysisError("shard-count-limit")
        children, definitions, dependency_entries, explanations = [], [], [], []
        child_analysis_ms: list[float] = []
        for filename, files in sorted(groups.items()):
            child_argv = child_command(parent, filename)
            validate_command(child_argv, root, execution_cwd)
            child_started = time.perf_counter_ns()
            child = inventory.analyze(
                root,
                child_argv,
                cwd=execution_cwd,
                limits=remaining_limits(),
                _baseline_snapshot=before,
            )
            child_analysis_ms.append(
                (time.perf_counter_ns() - child_started) / 1_000_000
            )
            if child["status"] != "analysis-complete":
                raise inventory.AnalysisError("child-collection-not-supported")
            if (child["workspace_digest"] != parent["workspace_digest"]
                    or child["project_identity"] != parent["project_identity"]
                    or child["runtime"] != parent["runtime"]):
                raise inventory.AnalysisError("child-context-changed")
            if child["dependencies"]["split_risks"] or child["dependencies"]["unknown"]:
                raise inventory.AnalysisError("child-independence-needs-review")
            shard_id = "module-" + inventory.digest(files)[:20]
            definitions.append({"id": shard_id, "checks": [child_argv], "covers": files})
            child_dependency_paths = adapters.dependency_paths(child["dependencies"])
            if child_dependency_paths is None:
                raise inventory.AnalysisError("adapter-dependencies-unsupported")
            # A whole-workspace candidate already covers the exact owner file,
            # including names that cannot be represented as glob literals.
            paths = set(inherited)
            if "**" in child_dependency_paths:
                paths.add("**")
            else:
                paths.update(files)
                paths.update(child_dependency_paths)
            normalized, error = dependencies.normalize_patterns(sorted(paths))
            if error:
                raise inventory.AnalysisError("dependency-path-not-representable")
            dependency_entries.append({"checks": [child_argv], "paths": list(normalized)})
            labels = adapters.inventory_labels(str(parent.get("adapter", "")), child["inventory"])
            if labels is None:
                raise inventory.AnalysisError("inventory-owner-unavailable")
            explanations.append({"id": shard_id, "modules": labels,
                                 "files": files, "dependency_candidates": list(normalized),
                                 "common_paths": candidates["common_paths"],
                                 "fixtures": child["collection"]["fixtures"]})
            children.append(child)
        comparison = equivalence(parent, children)
        fixture_counts = Counter(name for child in children for name in child["collection"]["fixtures"])
        if any(count > 1 for count in fixture_counts.values()):
            raise inventory.AnalysisError("shared-module-fixture-needs-review")
        raw = {"checks": [command["argv"]], "inventory": selected, "shards": definitions}
        _, error = shards._normalize_entry(raw, list(before), working_prefix=(
            "" if command["cwd"] == "." else command["cwd"]), parent_digests=set())
        if error:
            raise inventory.AnalysisError("proposal-incompatible-with-shard-schema")
        # Retain every old command's scope, widening exact collisions only.
        merged = {dependencies.manifest_group_digest(entry["checks"]):
                  {"checks": entry["checks"], "paths": list(entry["paths"])} for entry in old_entries}
        for entry in dependency_entries:
            key = dependencies.manifest_group_digest(entry["checks"])
            if key in merged:
                entry["paths"] = sorted(set(entry["paths"]) | set(merged[key]["paths"]))
            merged[key] = entry
        proposals = {".click/evidence-shards.json": {"version": 1, "entries": [raw]},
                     ".click/evidence-dependencies.json": {"version": 1, "entries":
                        [merged[key] for key in sorted(merged)]}}
        if any(len(json.dumps(value).encode()) > shards.MAX_CONFIG_BYTES for value in proposals.values()):
            raise inventory.AnalysisError("proposal-size-limit")
        if inventory.workspace_snapshot(root, limits) != before:
            raise inventory.AnalysisError("project-changed-during-proposal")
        remaining_limits()
        result.update(status="review-required" if existing else "proposal-ready",
                      proposal_ready=True, reasons=["existing-configuration-needs-review"] if existing else [],
                      proposals=proposals, equivalence=comparison, layout=explanations,
                      timing_samples={
                          "scope": "proposal-analysis-wall-not-test-savings",
                          "parent_analysis_ms": parent_analysis_ms,
                          "child_analysis_ms": child_analysis_ms,
                          "child_analysis_total_ms": sum(child_analysis_ms),
                          "workspace_file_records": len(before),
                          "workspace_snapshot_scans": 6 + 3 * len(children),
                          "avoided_initial_child_scans": len(children),
                          "limits": {
                              "files": limits.files,
                              "snapshot_bytes": limits.snapshot_bytes,
                              "modules": limits.modules,
                              "shards": MAX_SHARDS,
                          },
                      },
                      children=[{"argv": child["command"]["argv"], "cwd": child["command"]["cwd"],
                                 "inventory": child["inventory"],
                                 "inventory_digest": child["inventory_digest"]} for child in children])
    except (inventory.AnalysisError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, inventory.AnalysisError) else "proposal-unavailable"
        result.update(status="review-required", reasons=[reason], proposals={}, proposal_ready=False)
    return result
