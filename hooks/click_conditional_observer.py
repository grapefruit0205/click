"""Runner-attested JS observations with explicitly conditional reuse confidence.

Observations do not prove every possible JS input. Known dynamic inputs or
capture gaps still prevent reuse. Learning never executes an extra test: a
previous capture seeds before/after snapshots on the next requested execution.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

PROVIDER = "conditional-js-observation-v1"
FINGERPRINT_VERSION = 2
LIMITATION = "observed-inputs-only-completeness-unproven"
MAX_INPUTS = 4096
MAX_BYTES = 512 * 1024 * 1024
DIGEST = re.compile(r"^[0-9a-f]{64}$")
OPERATIONS = {"enumerate", "execute", "metadata", "read"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def modules():
    if __package__:
        from . import click_dependency_cache, click_node_observer
    else:
        import click_dependency_cache, click_node_observer
    return click_dependency_cache, click_node_observer


def source_digest():
    _, node = modules()
    return digest([node.source_digest(), *[
        hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in (
            "click_conditional_observer.py", "click_framework_observer.py", "click_observer_linux.py",
            "click_observer_process_tree.py")]])


def project_capture(raw, *, project, cwd, directory, truncated=False):
    """Conditional projection; retain ordinary external files as bound inputs.

Only the fixed Node bootstrap's proc/cgroup probes and ancestor metadata are
outside this confidence scope. The same probes after the acknowledgement are
ordinary inputs. No application read is removed because it is later written.
    """
    if __package__:
        from . import click_observer_linux as linux, click_observer_process_tree as processes
    else:
        import click_observer_linux as linux, click_observer_process_tree as processes
    tree = processes.inspect(raw, truncated=truncated)
    if not tree.complete or len(tree.process_ids) != 1 or not directory:
        return None
    root = Path(project).resolve()
    ready = str(Path(directory) / f"ready-{next(iter(tree.process_ids))}")
    pipe = str(Path(directory) / "endpoints.pipe")
    started, lines = False, []
    for line in tree.trace.decode("utf-8", errors="strict").splitlines():
        _, call_line = linux._strip_pid(line.strip())
        match = linux._CALL.match(call_line)
        if not match:
            lines.append(line)
            continue
        call, arguments, result = match.groups()
        path, _, _ = linux._decoded_path(arguments)
        if (path == "/dev/null" and call in linux._OPEN_CALLS and "O_RDONLY" in arguments
                and stat.S_ISCHR(os.stat("/dev/null").st_mode) and os.stat("/dev/null").st_rdev == os.makedev(1, 3)):
            continue
        if path == ready and call in {"access", "stat", "newfstatat"}:
            if linux._return_integer(result) == 0:
                started = True
            continue
        if path == pipe and call == "openat" and "O_WRONLY" in arguments:
            continue
        # libc probes kernel statx availability with a null pointer. EFAULT
        # supplies no filesystem value and is not a missing path dependency.
        if call == "statx" and arguments.startswith("0, NULL,") and result.startswith("-1 EFAULT"):
            continue
        if (call in {"newfstatat", "statx", "fstat"} and "AT_EMPTY_PATH" in arguments
                and re.match(r'[12]<pipe:\[[0-9]+\]>, ""', arguments)
                and linux._return_integer(result) == 0):
            # Captured stdout/stderr pipe metadata belongs to the runner's
            # fixed output transport. Stdin and arbitrary descriptors do not.
            continue
        if path == "/proc/sys/vm/overcommit_memory":
            # glibc's malloc reads this once, on the first large allocation,
            # which can fall on either side of the acknowledgement. It is the
            # allocator's own probe, never an application input, so it is not
            # a dynamic read in either phase. Every other pseudo-file read
            # after the acknowledgement remains dynamic.
            continue
        if not started and path and Path(path).is_absolute():
            bootstrap_probe = (path in {"/dev/null", "/proc/self/exe", "/proc/self/maps", "/proc/self/cgroup",
                                        "/proc/meminfo", "/proc/version_signature"}
                               or re.fullmatch(r"/proc/[0-9]+/(?:maps|cgroup)", path)
                               or path.startswith("/sys/fs/cgroup/") and path.endswith(("/memory.high", "/memory.max")))
            bootstrap_directory = (call in linux._METADATA_CALLS and "S_IFDIR" in arguments
                                   and not Path(path).is_relative_to(root))
            if bootstrap_probe or bootstrap_directory:
                continue
        lines.append(line)
    if not started:
        return None
    parsed = linux.parse_strace("\n".join(lines).encode(), workspace=root, initial_cwd=cwd,
                                allow_workspace_root=True, require_process_lifecycle=True)
    if parsed.unresolved_event_count or not parsed.process_tree_complete or not parsed.root_exec_observed:
        return None
    external = []
    for row in parsed.absolute_inputs:
        path = Path(row["path"])
        # Path traversal checks of workspace ancestors are part of the
        # conditional runtime baseline, not a claim about directory timestamps.
        if row["kind"] == "directory" and row["operations"] == ["metadata"] and (path == root or path in root.parents):
            continue
        if path.is_relative_to(root):
            continue
        # Pseudo-files and devices consumed by application code are dynamic.
        if path.parts[1:2] in [("proc",), ("sys",), ("dev",)]:
            return None
        external.append(row)
    return {"inputs": list(parsed.inputs), "external": external}


def external_snapshot(rows):
    if not isinstance(rows, list) or len(rows) > MAX_INPUTS:
        raise ValueError("external-input-limit")
    output, budget = [], [0]
    for row in rows:
        path = Path(row["path"])
        if not path.is_absolute() or path.parts[1:2] in [("proc",), ("sys",), ("dev",)]:
            raise ValueError("external-input-unsupported")
        aliases = []
        for part in [path, *path.parents]:
            if part.is_symlink():
                info = part.lstat()
                aliases.append([str(part), os.readlink(part), stat.S_IFMT(info.st_mode)])
        resolved = path.resolve(strict=False)
        normalized = {"path": str(resolved).lstrip("/"), "kind": row["kind"], "operations": row["operations"]}
        # openat(O_RDONLY) may open a directory without O_DIRECTORY (e.g.
        # libc locale discovery). Bind its actual metadata; getdents still
        # needs an explicit enumerate event and cannot be inferred here.
        if row["kind"] == "file" and resolved.is_dir():
            normalized["kind"] = "directory"
        output.append({**row, "digest": digest([aliases, fingerprint(Path("/"), normalized, budget)])})
    return output


def external_valid(rows):
    return bool(isinstance(rows, list) and len(rows) <= MAX_INPUTS and
                all(isinstance(row, dict) and set(row) == {"path", "kind", "operations", "digest"}
                    and isinstance(row["path"], str) and row["path"].startswith("/")
                    and records_valid([{**row, "path": row["path"][1:]}]) for row in rows)
                and [row["path"] for row in rows] == sorted(set(row["path"] for row in rows)))


def seed_valid(rows):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_INPUTS:
        return False
    paths = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "kind", "operations"}:
            return False
        path, operations = row.get("path"), row.get("operations")
        if (not isinstance(path, str) or not path or "\\" in path or "\x00" in path
                or PurePosixPath(path).is_absolute() or PurePosixPath(path).as_posix() != path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or row.get("kind") not in {"file", "directory", "missing"}
                or not isinstance(operations, list) or not operations
                or any(not isinstance(op, str) or op not in OPERATIONS for op in operations)
                or operations != sorted(set(operations))):
            return False
        paths.append(path)
    return paths == sorted(set(paths))


def seed(rows):
    if not isinstance(rows, list):
        raise ValueError("input-seed-unavailable")
    # OS directory paths use a trailing slash; snapshots use one canonical name.
    result = [{"path": row["path"].removesuffix("/"), "kind": row["kind"],
               "operations": row["operations"]} for row in rows]
    if not seed_valid(result):
        raise ValueError("input-seed-invalid")
    return result


def fingerprint(root, row, budget):
    target = root / row["path"]
    # Check every component, including a missing leaf below a symlink.
    for candidate in [target, *target.parents]:
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError("symlink-input")
    try:
        before = target.lstat()
    except FileNotFoundError:
        if row["kind"] != "missing":
            raise ValueError("input-kind-changed")
        return digest(["missing"])
    def metadata(info):
        return [getattr(info, field) for field in (
            "st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns")]
    # Same content identity as the native observer: type/permission bits plus
    # content, membership or size. Inode, ownership and timestamps are runtime
    # assumptions, so an equal-content rewrite keeps the conditional receipt.
    payload = [FINGERPRINT_VERSION, before.st_mode]
    if stat.S_ISDIR(before.st_mode) and row["kind"] == "directory":
        with os.scandir(target) as entries:
            payload.append(sorted((entry.name,
                                   stat.S_IFMT(entry.stat(follow_symlinks=False).st_mode)) for entry in entries))
    elif stat.S_ISREG(before.st_mode) and row["kind"] == "file":
        if not set(row["operations"]) & {"read", "execute"}:
            payload.append(before.st_size)
        if set(row["operations"]) & {"read", "execute"}:
            if before.st_size > 256 * 1024 * 1024:
                raise ValueError("input-size-limit")
            budget[0] += before.st_size
            if budget[0] > MAX_BYTES:
                raise ValueError("input-size-limit")
            hasher = hashlib.sha256()
            with target.open("rb") as stream:
                while chunk := stream.read(128 * 1024):
                    hasher.update(chunk)
            payload.append(hasher.hexdigest())
    else:
        raise ValueError("input-kind-changed")
    if metadata(target.lstat()) != metadata(before):
        raise ValueError("input-raced")
    return digest(payload)


def snapshot(project, rows):
    root, budget = Path(project).resolve(strict=True), [0]
    return [{**row, "digest": fingerprint(root, row, budget)} for row in seed(rows)]


def records_valid(rows):
    return (isinstance(rows, list)
            and all(isinstance(row, dict) and set(row) == {"path", "kind", "operations", "digest"}
                    and isinstance(row["digest"], str) and DIGEST.fullmatch(row["digest"]) for row in rows)
            and seed_valid([{key: row[key] for key in ("path", "kind", "operations")} for row in rows]))


def current(project, rows):
    if not records_valid(rows):
        return False
    try:
        return snapshot(project, rows) == rows
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False


def eligible_record(record):
    dependencies, node = modules()
    if not isinstance(record, dict):
        return False
    capture, runtime = record.get("capture"), record.get("runtime")
    projection = record.get("conditional_capture")
    try:
        if not isinstance(projection, dict) or set(projection) != {"inputs", "external"} or not seed_valid(seed(projection["inputs"])):
            return False
    except (KeyError, ValueError, TypeError):
        return False
    return bool(dependencies.shadow_observer_record_is_valid(capture)
                and node.valid(runtime) and runtime["capture_complete"]
                and not any(runtime["counts"].values())
                and runtime["reasons"] == ["engine-input-coverage-incomplete"])


def valid(value):
    dependencies, _ = modules()
    if not isinstance(value, dict) or set(value) != {
            "provider", "status", "confidence", "limitation", "runtime_inputs_complete",
            "paths", "inputs", "external_inputs", "binding", "runtime"}:
        return False
    if (value.get("provider") != PROVIDER or value.get("status") != "conditional"
            or value.get("confidence") != "conditional" or value.get("limitation") != LIMITATION
            or value.get("runtime_inputs_complete") is not False or not records_valid(value.get("inputs"))
            or not external_valid(value.get("external_inputs"))):
        return False
    binding, runtime = value.get("binding"), value.get("runtime")
    if (not isinstance(binding, dict) or set(binding) != dependencies.AUTHORITATIVE_BINDING_FIELDS
            or type(binding.get("mutation_revision")) is not int or binding["mutation_revision"] < 0
            or any(not isinstance(binding[key], str) or not DIGEST.fullmatch(binding[key])
                   for key in binding if key != "mutation_revision")
            or not isinstance(runtime, dict) or set(runtime) != {"node", "node_digest", "collector_digest", "backend_path", "backend_digest"}
            or any(not isinstance(runtime[key], str) or not DIGEST.fullmatch(runtime[key])
                   for key in ("node_digest", "collector_digest", "backend_digest"))
            or any(not isinstance(runtime[key], str) or not Path(runtime[key]).is_absolute() for key in ("node", "backend_path"))):
        return False
    return value["paths"] == sorted(row["path"] + ("/" if row["kind"] == "directory" else "") for row in value["inputs"])


def matches(value, *, project, binding, **_):
    if not valid(value):
        return False
    dependencies, node = modules()
    if (not isinstance(binding, dict) or set(binding) != dependencies.AUTHORITATIVE_CURRENT_BINDING_FIELDS
            or any(value["binding"].get(key) != item for key, item in binding.items())):
        return False
    runtime = value["runtime"]
    try:
        return (runtime["collector_digest"] == source_digest()
                and stat.S_ISCHR(os.stat("/dev/null").st_mode) and os.stat("/dev/null").st_rdev == os.makedev(1, 3)
                and node.digest_file(Path(runtime["node"])) == runtime["node_digest"]
                and node.digest_file(Path(runtime["backend_path"])) == runtime["backend_digest"]
                and external_snapshot(value["external_inputs"]) == value["external_inputs"]
                and current(project, value["inputs"]))
    except (OSError, ValueError):
        return False


def issue(record, *, before, external_before, project, context, secret, node_path, backend_path):
    if not eligible_record(record) or not before or not node_path or not backend_path:
        return None
    if __package__:
        from . import click_authoritative_observer as signing
    else:
        import click_authoritative_observer as signing
    try:
        projection = record["conditional_capture"]
        after = snapshot(project, projection["inputs"])
        external_after = external_snapshot(projection["external"])
        if after != before or external_after != external_before:
            return None
        runtime = record["runtime"]
        value = {"provider": PROVIDER, "status": "conditional", "confidence": "conditional",
                 "limitation": LIMITATION, "runtime_inputs_complete": False,
                 "paths": sorted(row["path"] + ("/" if row["kind"] == "directory" else "") for row in before),
                 "inputs": before, "external_inputs": external_before, "binding": signing._binding(context),
                 "runtime": {"node": str(Path(node_path).resolve()), "node_digest": runtime["runtime_digest"],
                             "collector_digest": source_digest(), "backend_path": str(Path(backend_path).resolve()),
                             "backend_digest": record["capture"]["backend"]["digest"]}}
        return signing.signed_envelope(value, secret) if valid(value) else None
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return None


def verify(envelope, *, secret, expected_binding):
    if __package__:
        from . import click_authoritative_observer as signing
    else:
        import click_authoritative_observer as signing
    if (not isinstance(envelope, dict) or set(envelope) != signing.ENVELOPE_FIELDS
            or type(envelope.get("version")) is not int or envelope["version"] != 1
            or not isinstance(envelope.get("attestation"), str) or not DIGEST.fullmatch(envelope["attestation"])
            or not valid(envelope.get("observation")) or not isinstance(expected_binding, dict)
            or set(expected_binding) != signing.FULL_BINDING_FIELDS):
        return None
    value = envelope["observation"]
    if (not hmac.compare_digest(envelope["attestation"], signing._attestation(value, secret))
            or any(value["binding"].get(key) != item for key, item in expected_binding.items())):
        return None
    return json.loads(json.dumps(value))
