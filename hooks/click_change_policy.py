"""Repository-declared safe-change policy for cross-revision evidence reuse.

This module deliberately does not infer dependencies.  It compares the exact
workspace state that passed verification with the current Git workspace, then
allows reuse only when every net changed path is covered by the same committed
policy entry.  Missing, malformed, racing, or unsupported state fails closed.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

if __package__:
    from . import click_dependency_cache
    from . import click_input_policy
else:  # Executed directly from the bundled hooks directory.
    import click_dependency_cache
    import click_input_policy


CONFIG_RELATIVE_PATH = ".click/evidence-reuse.json"
CONFIG_VERSION = 1
PROVIDER_NAME = "repository-safe-change-policy-v1"
INPUT_PROVIDER_NAME = "repository-scoped-input-policy-v2"
SNAPSHOT_PROVIDER_NAME = "git-effective-workspace-v1"
MAX_CONFIG_BYTES = 256 * 1024
MAX_DIRTY_PATHS = 512
MAX_CHANGED_PATHS = 4096
MAX_PATH_BYTES = 4096
PROTECTED_POLICY_PATHS = (
    CONFIG_RELATIVE_PATH,
    click_dependency_cache.CONFIG_RELATIVE_PATH,
    ".click/evidence-shards.json",
)
RECEIPT_FIELDS = frozenset(
    {"provider", "config_digest", "entry_digest", "patterns", "baseline"}
)
SNAPSHOT_FIELDS = frozenset(
    {"version", "provider", "object_format", "head", "overrides", "digest"}
)
OVERRIDE_FIELDS = frozenset({"path", "identity"})
DECISION_CONTEXT_FIELDS = frozenset({"source_key", "revision", "git_root", "tree_digest"})

GitCapture = Callable[[Path, list[str]], bytes | None]
_preflight = ContextVar("click_owner_policy_preflight", default=None)


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_config_bytes(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _is_digest(value: Any) -> bool:
    return bool(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value))


def _safe_relative_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
    ):
        return False
    try:
        encoded = os.fsencode(value)
    except UnicodeEncodeError:
        return False
    if len(encoded) > MAX_PATH_BYTES:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _group_digest(checks: Any) -> str:
    if not isinstance(checks, list) or not checks:
        return ""
    payload: list[dict[str, list[str]]] = []
    for check in checks:
        argv = check.get("argv") if isinstance(check, dict) else None
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            return ""
        payload.append({"argv": list(argv)})
    return _digest({"checks": payload})


def _manifest_group_digest(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return ""
    return _group_digest(
        [
            {"argv": argv}
            for argv in value
            if isinstance(argv, list)
        ]
    ) if all(isinstance(argv, list) for argv in value) else ""


def _decode_paths(output: bytes) -> list[str] | None:
    paths = [os.fsdecode(item) for item in output.split(b"\0") if item]
    if (
        len(paths) > MAX_CHANGED_PATHS
        or len(set(paths)) != len(paths)
        or any(not _safe_relative_path(path) for path in paths)
    ):
        return None
    return paths


def _policy_file_matches(root: Path, committed: bytes) -> bool:
    target = root / CONFIG_RELATIVE_PATH
    try:
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            return False
        current = target.read_bytes()
    except OSError:
        return False
    return _canonical_config_bytes(current) == _canonical_config_bytes(committed)


def _load_policy(
    cwd: Path, git_capture: GitCapture
) -> tuple[Path, str, str, dict[str, dict[str, Any]], bytes] | None:
    root_output = git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if root_output is None:
        return None
    root = Path(os.fsdecode(root_output.strip()))
    head_output = git_capture(root, ["rev-parse", "--verify", "HEAD"])
    if head_output is None:
        return None
    head = os.fsdecode(head_output.strip())
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None:
        return None
    committed = git_capture(root, ["show", f"{head}:{CONFIG_RELATIVE_PATH}"])
    if (
        committed is None
        or len(committed) > MAX_CONFIG_BYTES
        or not _policy_file_matches(root, committed)
    ):
        return None
    canonical_raw = _canonical_config_bytes(committed)
    try:
        value = json.loads(canonical_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) != {"version", "entries"}:
        return None
    if type(value.get("version")) is not int or value["version"] not in {1, 2}:
        return None
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        return None

    entries: dict[str, dict[str, Any]] = {}
    for entry in raw_entries:
        expected = {"checks", "reuse_if_only_changed"}
        if value["version"] == 2:
            expected.add("inputs")
        if not isinstance(entry, dict) or set(entry) != expected:
            return None
        group_digest = _manifest_group_digest(entry.get("checks"))
        patterns, error = click_dependency_cache.normalize_patterns(
            entry.get("reuse_if_only_changed")
        )
        if (
            not group_digest
            or group_digest in entries
            or error
            or patterns is None
            or any(
                click_dependency_cache.path_matches(pattern, protected)
                for pattern in patterns
                for protected in PROTECTED_POLICY_PATHS
            )
        ):
            return None
        descriptor: dict[str, Any] = {"patterns": patterns}
        if value["version"] == 2:
            inputs, input_error = click_dependency_cache.normalize_patterns(entry["inputs"])
            if input_error or inputs is None:
                return None
            descriptor["inputs"] = inputs
        entries[group_digest] = descriptor
    return root, head, hashlib.sha256(canonical_raw).hexdigest(), entries, committed


def _dirty_paths(root: Path, git_capture: GitCapture) -> list[str] | None:
    unmerged = git_capture(root, ["ls-files", "--unmerged", "-z"])
    if unmerged is None or unmerged:
        return None
    outputs = (
        git_capture(
            root,
            ["diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--"],
        ),
        git_capture(
            root,
            ["diff", "--name-only", "-z", "--no-ext-diff", "--"],
        ),
        git_capture(root, ["ls-files", "--others", "--exclude-standard", "-z"]),
    )
    if any(output is None for output in outputs):
        return None
    paths: set[str] = set()
    for output in outputs:
        assert output is not None
        decoded = _decode_paths(output)
        if decoded is None:
            return None
        paths.update(decoded)
        if len(paths) > MAX_DIRTY_PATHS:
            return None
    return sorted(paths)


def _object_format(root: Path, git_capture: GitCapture) -> str | None:
    output = git_capture(root, ["rev-parse", "--show-object-format"])
    if output is None:
        return None
    value = os.fsdecode(output.strip())
    return value if value in {"sha1", "sha256"} else None


def _literal_pathspec(relative: str) -> str:
    return f":(top,literal){relative}"


def _tree_identities(
    root: Path,
    head: str,
    paths: list[str],
    *,
    object_format: str,
    git_capture: GitCapture,
) -> dict[str, str] | None:
    identities: dict[str, str] = {}
    expected_oid_length = 40 if object_format == "sha1" else 64
    for offset in range(0, len(paths), 128):
        chunk = paths[offset : offset + 128]
        output = git_capture(
            root,
            [
                "ls-tree",
                "-z",
                "--full-tree",
                head,
                "--",
                *(_literal_pathspec(path) for path in chunk),
            ],
        )
        if output is None:
            return None
        for item in output.split(b"\0"):
            if not item:
                continue
            try:
                metadata, raw_path = item.split(b"\t", 1)
                mode, kind, oid = os.fsdecode(metadata).split(" ", 2)
            except ValueError:
                return None
            relative = os.fsdecode(raw_path)
            if (
                relative not in chunk
                or relative in identities
                or mode not in {"100644", "100755"}
                or kind != "blob"
                or re.fullmatch(
                    rf"[0-9a-f]{{{expected_oid_length}}}", oid
                ) is None
            ):
                return None
            identities[relative] = f"{mode}:{oid}"
    return identities


def _filemode_is_trusted(root: Path, git_capture: GitCapture) -> bool | None:
    output = git_capture(root, ["config", "--bool", "core.filemode"])
    if output is None:
        return None
    value = os.fsdecode(output.strip()).lower()
    if value not in {"true", "false"}:
        return None
    return value == "true"


def _workspace_identity(
    root: Path,
    relative: str,
    *,
    object_format: str,
    tracked_identity: str | None,
    filemode_is_trusted: bool,
) -> str | None:
    target = root / relative
    try:
        before = target.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return None
    if not stat.S_ISREG(before.st_mode):
        return None
    if filemode_is_trusted:
        mode = "100755" if before.st_mode & 0o111 else "100644"
    elif tracked_identity is not None:
        mode = tracked_identity.split(":", 1)[0]
    else:
        mode = "100644"
    try:
        hasher = hashlib.new(object_format)
    except ValueError:
        return None
    hasher.update(f"blob {before.st_size}\0".encode())
    try:
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(128 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        after = target.lstat()
    except OSError:
        return None
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        return None
    return f"{mode}:{hasher.hexdigest()}"


def _snapshot(
    root: Path,
    *,
    expected_head: str,
    git_capture: GitCapture,
) -> dict[str, Any] | None:
    head_output = git_capture(root, ["rev-parse", "--verify", "HEAD"])
    if head_output is None or os.fsdecode(head_output.strip()) != expected_head:
        return None
    object_format = _object_format(root, git_capture)
    dirty = _dirty_paths(root, git_capture)
    filemode_is_trusted = _filemode_is_trusted(root, git_capture)
    if object_format is None or dirty is None or filemode_is_trusted is None:
        return None
    tracked = _tree_identities(
        root,
        expected_head,
        dirty,
        object_format=object_format,
        git_capture=git_capture,
    )
    if tracked is None:
        return None
    overrides: list[dict[str, str]] = []
    for relative in dirty:
        identity = _workspace_identity(
            root,
            relative,
            object_format=object_format,
            tracked_identity=tracked.get(relative),
            filemode_is_trusted=filemode_is_trusted,
        )
        if identity is None:
            return None
        overrides.append({"path": relative, "identity": identity})
    payload = {
        "version": 1,
        "provider": SNAPSHOT_PROVIDER_NAME,
        "object_format": object_format,
        "head": expected_head,
        "overrides": overrides,
    }
    return {**payload, "digest": _digest(payload)}


def snapshot_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS:
        return False
    object_format = value.get("object_format")
    head = value.get("head")
    overrides = value.get("overrides")
    oid_length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if (
        not isinstance(value.get("version"), int)
        or isinstance(value.get("version"), bool)
        or value.get("version") != 1
        or value.get("provider") != SNAPSHOT_PROVIDER_NAME
        or not oid_length
        or not isinstance(head, str)
        or re.fullmatch(rf"[0-9a-f]{{{oid_length}}}", head) is None
        or not isinstance(overrides, list)
        or len(overrides) > MAX_DIRTY_PATHS
    ):
        return False
    paths: list[str] = []
    for override in overrides:
        if not isinstance(override, dict) or set(override) != OVERRIDE_FIELDS:
            return False
        relative = override.get("path")
        identity = override.get("identity")
        if not _safe_relative_path(relative) or not isinstance(identity, str):
            return False
        if identity != "missing" and re.fullmatch(
            rf"(?:100644|100755):[0-9a-f]{{{oid_length}}}", identity
        ) is None:
            return False
        paths.append(relative)
    payload = {field: value[field] for field in SNAPSHOT_FIELDS - {"digest"}}
    return bool(
        paths == sorted(set(paths))
        and _is_digest(value.get("digest"))
        and value.get("digest") == _digest(payload)
    )


def receipt_is_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    scoped = value.get("provider") == INPUT_PROVIDER_NAME
    fields = RECEIPT_FIELDS | {"inputs", "inputs_digest"} if scoped else RECEIPT_FIELDS
    if set(value) != fields:
        return False
    if scoped:
        inputs, input_error = click_dependency_cache.normalize_patterns(value.get("inputs"))
        if input_error or inputs is None or list(inputs) != value.get("inputs") or not _is_digest(value.get("inputs_digest")):
            return False
    patterns, error = click_dependency_cache.normalize_patterns(value.get("patterns"))
    return bool(
        value.get("provider") in {PROVIDER_NAME, INPUT_PROVIDER_NAME}
        and _is_digest(value.get("config_digest"))
        and _is_digest(value.get("entry_digest"))
        and not error
        and patterns is not None
        and list(patterns) == value.get("patterns")
        and not any(
            click_dependency_cache.path_matches(pattern, protected)
            for pattern in patterns
            for protected in PROTECTED_POLICY_PATHS
        )
        and snapshot_is_valid(value.get("baseline"))
    )


def inputs_are_current(root: Path, receipt: Any) -> bool:
    if not receipt_is_valid(receipt):
        return False
    if receipt["provider"] != INPUT_PROVIDER_NAME:
        return True
    return click_input_policy.snapshot(root, receipt["inputs"]) == receipt["inputs_digest"]


def requires_input_receipt(root: Path, check_digest: str) -> bool:
    """A scoped check with an incomplete baseline must not take exact reuse."""
    target = root / CONFIG_RELATIVE_PATH
    try:
        if target.stat().st_size > MAX_CONFIG_BYTES or target.is_symlink():
            return True
        value = json.loads(target.read_bytes())
        return bool(
            isinstance(value, dict) and value.get("version") == 2
            and any(isinstance(entry, dict)
                    and _manifest_group_digest(entry.get("checks")) == check_digest
                    for entry in value.get("entries", []))
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError):
        return True


def same_policy_inputs(first: Any, second: Any) -> bool:
    return bool(receipt_is_valid(first) and receipt_is_valid(second) and all(
        first.get(field) == second.get(field)
        for field in ("provider", "config_digest", "entry_digest", "patterns", "inputs", "inputs_digest")
    ))


def changed_paths_are_valid(value: Any, *, maximum: int = MAX_CHANGED_PATHS) -> bool:
    return bool(
        isinstance(value, list)
        and len(value) <= maximum
        and all(_safe_relative_path(path) for path in value)
        and value == sorted(set(value))
    )


def receipts_for_groups(
    cwd: Path,
    grouped_checks: dict[str, list[dict[str, Any]]],
    *,
    git_capture: GitCapture,
    scoped_only: bool = False,
) -> dict[str, dict[str, Any]]:
    """Capture the current policy and effective Git baseline for exact groups."""
    loaded = _load_policy(cwd, git_capture)
    if loaded is None:
        return {}
    root, head, config_digest, entries, committed = loaded
    if scoped_only:
        entries = {key: value for key, value in entries.items() if "inputs" in value}
        if not entries:
            return {}
    baseline = _snapshot(root, expected_head=head, git_capture=git_capture)
    if baseline is None or not _policy_file_matches(root, committed):
        return {}
    receipts: dict[str, dict[str, Any]] = {}
    for source_key, checks in grouped_checks.items():
        group_digest = _group_digest(checks)
        descriptor = entries.get(group_digest)
        if not isinstance(source_key, str) or descriptor is None:
            continue
        patterns = descriptor["patterns"]
        entry_payload = {
            "checks": [check["argv"] for check in checks],
            "reuse_if_only_changed": list(patterns),
        }
        receipts[source_key] = {
            "provider": PROVIDER_NAME,
            "config_digest": config_digest,
            "entry_digest": _digest(entry_payload),
            "patterns": list(patterns),
            "baseline": baseline,
        }
        if "inputs" in descriptor:
            inputs = list(descriptor["inputs"])
            fingerprint = click_input_policy.snapshot(root, inputs)
            if fingerprint is None or click_input_policy.snapshot(root, inputs) != fingerprint:
                receipts.pop(source_key)
                continue
            entry_payload["inputs"] = inputs
            receipts[source_key].update(
                provider=INPUT_PROVIDER_NAME, inputs=inputs, inputs_digest=fingerprint,
                entry_digest=_digest(entry_payload),
            )
    return receipts


def _changed_paths(
    root: Path,
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    git_capture: GitCapture,
) -> list[str] | None:
    commit_diff = git_capture(
        root,
        [
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            baseline["head"],
            current["head"],
            "--",
        ],
    )
    if commit_diff is None:
        return None
    committed_paths = _decode_paths(commit_diff)
    if committed_paths is None:
        return None
    baseline_overrides = {
        override["path"]: override["identity"]
        for override in baseline["overrides"]
    }
    current_overrides = {
        override["path"]: override["identity"]
        for override in current["overrides"]
    }
    candidates = sorted(
        set(committed_paths) | set(baseline_overrides) | set(current_overrides)
    )
    if len(candidates) > MAX_CHANGED_PATHS:
        return None
    baseline_tree = _tree_identities(
        root,
        baseline["head"],
        candidates,
        object_format=baseline["object_format"],
        git_capture=git_capture,
    )
    current_tree = _tree_identities(
        root,
        current["head"],
        candidates,
        object_format=current["object_format"],
        git_capture=git_capture,
    )
    filemode_is_trusted = _filemode_is_trusted(root, git_capture)
    if baseline_tree is None or current_tree is None or filemode_is_trusted is None:
        return None

    changed: list[str] = []
    for relative in candidates:
        baseline_identity = baseline_overrides.get(
            relative, baseline_tree.get(relative, "missing")
        )
        if relative in current_overrides:
            current_identity = current_overrides[relative]
        elif relative in current_tree:
            current_identity = current_tree[relative]
        else:
            current_identity = _workspace_identity(
                root,
                relative,
                object_format=current["object_format"],
                tracked_identity=None,
                filemode_is_trusted=filemode_is_trusted,
            )
            if current_identity is None:
                return None
        if baseline_identity != current_identity:
            changed.append(relative)
    return changed


def _decision_context_is_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == DECISION_CONTEXT_FIELDS
        and _is_digest(value.get("source_key"))
        and isinstance(value.get("revision"), int)
        and not isinstance(value.get("revision"), bool)
        and value["revision"] >= 0
        and isinstance(value.get("git_root"), str)
        and bool(value["git_root"])
        and _is_digest(value.get("tree_digest"))
    )


def _same_git_root(left: str, right: Path) -> bool:
    """Treat platform path aliases as one root without weakening isolation."""
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def _decision_payload(
    baseline: dict[str, Any], current: dict[str, Any], changed_paths: list[str],
    check_digest: str, decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "provider": current["provider"],
        "entry_digest": current["entry_digest"],
        "from_snapshot": baseline["baseline"]["digest"],
        "to_snapshot": current["baseline"]["digest"],
        "changed_paths": changed_paths,
        "check_digest": check_digest,
        "baseline_receipt_digest": _digest(baseline),
        "context": decision_context,
    }


def reuse_decision_matches(
    decision: Any, baseline_receipt: Any, *, check_digest: str,
    decision_context: Any,
) -> bool:
    """Validate a produced decision against its source and captured current scope.

    This is an in-memory consistency check, not issuer authentication. The
    caller still owns current execution bindings and post-decision drift checks.
    """
    if (
        not isinstance(decision, dict)
        or decision.get("status") != "reuse"
        or not _decision_context_is_valid(decision_context)
        or not _is_digest(check_digest)
        or not receipt_is_valid(baseline_receipt)
        or not receipt_is_valid(decision.get("receipt"))
        or not changed_paths_are_valid(decision.get("changed_paths"))
        or not _is_digest(decision.get("decision_digest"))
    ):
        return False
    current = decision["receipt"]
    changed = decision["changed_paths"]
    if not same_policy_inputs(current, baseline_receipt) or any(
        path in PROTECTED_POLICY_PATHS
        or not any(click_dependency_cache.path_matches(pattern, path) for pattern in current["patterns"])
        for path in changed
    ):
        return False
    return decision["decision_digest"] == _digest(_decision_payload(
        baseline_receipt, current, changed, check_digest, decision_context,
    ))


def decide(
    cwd: Path,
    checks: list[dict[str, Any]],
    baseline_receipt: Any,
    *,
    git_capture: GitCapture,
    decision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a policy decision; execution reuse also requires a bound context."""
    fallback = {
        "status": "unknown",
        "reason": "preflight-unavailable",
        "changed_paths": [],
        "decision_digest": "",
        "receipt": {},
    }
    if not receipt_is_valid(baseline_receipt) or (
        decision_context is not None and not _decision_context_is_valid(decision_context)
    ):
        return fallback
    shared = _preflight.get()
    if shared is not None and shared["cwd"] == cwd.resolve():
        current = shared["receipts"].get(_group_digest(checks))
    else:
        shared = None
        current = receipts_for_groups(
            cwd, {"candidate": checks}, git_capture=git_capture
        ).get("candidate")
    if not receipt_is_valid(current):
        return fallback
    assert isinstance(current, dict)
    if (
        current["provider"] != baseline_receipt["provider"]
        or current.get("inputs") != baseline_receipt.get("inputs")
        or
        current["config_digest"] != baseline_receipt["config_digest"]
        or current["entry_digest"] != baseline_receipt["entry_digest"]
        or current["patterns"] != baseline_receipt["patterns"]
    ):
        return {
            **fallback,
            "status": "rerun",
            "reason": "policy-changed",
            "receipt": current,
        }
    if current.get("inputs_digest") != baseline_receipt.get("inputs_digest"):
        return {**fallback, "status": "rerun", "reason": "declared-input-changed", "receipt": current}
    if shared is None:
        loaded = _load_policy(cwd, git_capture)
        if loaded is None:
            return fallback
        root = loaded[0]
    else:
        root = shared["root"]
    if decision_context is not None and not _same_git_root(
        decision_context["git_root"], root
    ):
        return fallback
    cache_key = (baseline_receipt["baseline"]["digest"], current["baseline"]["digest"])
    changes = shared["changes"] if shared is not None else {}
    if cache_key not in changes:
        changes[cache_key] = _changed_paths(
            root, baseline_receipt["baseline"], current["baseline"],
            git_capture=git_capture,
        )
    changed = changes[cache_key]
    if changed is None:
        return fallback
    decision_digest = _digest(_decision_payload(
        baseline_receipt, current, changed, _group_digest(checks), decision_context,
    ))
    protected_changed = any(path in PROTECTED_POLICY_PATHS for path in changed)
    unmatched = [
        path
        for path in changed
        if not any(
            click_dependency_cache.path_matches(pattern, path)
            for pattern in current["patterns"]
        )
    ]
    return {
        "status": "rerun" if protected_changed or unmatched else "reuse",
        "reason": (
            "protected-policy-path-changed"
            if protected_changed
            else "path-not-declared-safe"
            if unmatched
            else "no-net-change"
            if not changed
            else "all-paths-declared-safe"
        ),
        "changed_paths": changed,
        "decision_digest": decision_digest,
        "receipt": current,
    }


def decide_groups(
    cwd: Path,
    grouped_checks: dict[str, list[dict[str, Any]]],
    baselines: dict[str, Any],
    *,
    git_capture: GitCapture,
    decision_contexts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Share one policy/Git preflight, never a persistent reuse authorization.

    The verification caller still confirms workspace, runtime and scoped input
    fingerprints after all provisional promotions.
    """
    loaded = _load_policy(cwd, git_capture)
    receipts = receipts_for_groups(cwd, grouped_checks, git_capture=git_capture)
    shared = {
        "cwd": cwd.resolve(), "root": loaded[0] if loaded else cwd.resolve(),
        "receipts": {_group_digest(grouped_checks[key]): value for key, value in receipts.items()},
        "changes": {},
    }
    token = _preflight.set(shared)
    try:
        return {
            key: decide(cwd, checks, baselines.get(key), git_capture=git_capture,
                        decision_context=decision_contexts[key])
            for key, checks in grouped_checks.items()
        }
    finally:
        _preflight.reset(token)


group_digest = _group_digest
