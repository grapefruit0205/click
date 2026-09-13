"""Build the exact-ABI Node state reader outside the target repository.

The cache is an executable artifact, never an input-completeness receipt.
Unknown binaries, missing tools or failed probes leave ordinary execution open.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

NODE_DIGEST = "3517c2df0b2f8cd7f422b4b8450ef81c6889f08eb03e281d6de9079b15e6a327"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def prepare(node: Path, workspace: Path, environment: dict) -> tuple[Path, str] | None:
    if os.name == "nt":
        # The companion is built against the Linux binary's exact layout with a
        # POSIX toolchain; without it, consumed random state stays unavailable
        # and the runtime record says so (`input-native-state-unavailable`).
        return None
    try:
        if digest(node) != NODE_DIGEST:
            return None
        compiler = Path(shutil.which("c++", path=environment.get("PATH")) or "").resolve(strict=True)
        if workspace.resolve() in compiler.parents:
            return None
        headers = node.parent.parent / "include/node"
        source = Path(__file__).with_suffix(".cc")
        header_files = sorted(headers.glob("*.h"))
        if not (headers / "node.h").is_file() or not header_files:
            return None
        identity = {"source": digest(source), "node": NODE_DIGEST, "compiler": digest(compiler),
                    "headers": [(path.name, digest(path)) for path in header_files],
                    "flags": ["-std=c++20", "-shared", "-fPIC", "-O2"]}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        directory = Path(tempfile.gettempdir()) / ("click-node-state-" + str(os.getuid()) + "-" + key[:32])
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.getuid() or directory.is_symlink()):
            return None
        artifact = directory / "state.node"
        manifest = directory / "state.json"
        try:
            if (not artifact.is_symlink() and not manifest.is_symlink()
                    and manifest.stat().st_size <= 4096):
                previous = json.loads(manifest.read_text())
                current_digest = digest(artifact)
                if previous == {"build": key, "artifact": current_digest}:
                    return artifact, current_digest
        except (OSError, ValueError):
            pass
        clean = {name: value for name, value in environment.items()
                 if not name.startswith(("LD_", "DYLD_")) and name not in {
                     "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
                     "COMPILER_PATH", "GCC_EXEC_PREFIX", "NODE_OPTIONS", "NODE_PATH",
                     "CLICK_NODE_OBSERVER_DIRECTORY", "CXXFLAGS", "LDFLAGS",
                 }}
        with tempfile.TemporaryDirectory(prefix="build-", dir=directory) as temporary:
            output = Path(temporary) / "state.node"
            built = subprocess.run([str(compiler), *identity["flags"], "-I" + str(headers), str(source), "-o", str(output)],
                                   cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if built.returncode:
                return None
            # Validate the private layout in a disposable process, before any
            # user check can load it. No random call is added to the real check.
            probe = "const s=require(process.argv[1]);const a=s.randomState(Math.random);const r=Math.random();const b=s.randomState(Math.random);if(!a.startsWith('v8-xorshift128-cache64-v1:0:0000000000000000:0000000000000000:')||r!==0.9044192244068718||!b.startsWith('v8-xorshift128-cache64-v1:63:'))process.exitCode=1;"
            checked = subprocess.run([str(node), "--random-seed=12345", "-e", probe, str(output)],
                                     cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if checked.returncode or digest(node) != NODE_DIGEST or digest(source) != identity["source"]:
                return None
            artifact_digest = digest(output)
            metadata = Path(temporary) / "state.json"
            metadata.write_text(json.dumps({"build": key, "artifact": artifact_digest}))
            os.replace(output, artifact)
            os.replace(metadata, manifest)
            return artifact, artifact_digest
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
