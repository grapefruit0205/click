"""Build the exact-ABI Node state reader outside the target repository.

The cache is an executable artifact, never an input-completeness receipt.
Unknown binaries, missing tools or failed probes leave ordinary execution open.

Linux compiles the reader with the host C++ compiler against the headers
shipped next to the Node binary. Windows compiles it with the MSVC toolchain
on PATH (a developer prompt, as the Python companion already requires),
against the headers the Node MSI or a node-gyp cache installed, and links it
through an import library generated from node.exe's own export table, so no
download is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

# Official Node 22.23.2 binaries, by platform (nodejs.org SHASUMS256.txt).
NODE_DIGESTS = {
    "linux": "3517c2df0b2f8cd7f422b4b8450ef81c6889f08eb03e281d6de9079b15e6a327",
    "win32": "0d0f5e39f9f3d9587bc19f73eab3c2c9c4903fd02d6dbf9c853dd81b3d95fad4",
}
NODE_DIGEST = NODE_DIGESTS.get(sys.platform, NODE_DIGESTS["linux"])
NODE_VERSION = "22.23.2"
WINDOWS_TOOLS = ("cl", "link", "lib", "dumpbin")
_EXPORT_LINE = re.compile(r"^\s*\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+(\S+)")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def prepare(node: Path, workspace: Path, environment: dict) -> tuple[Path, str] | None:
    if os.name == "nt":
        return _prepare_windows(node, workspace, environment)
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


def _windows_headers(node: Path, environment: dict) -> Path | None:
    """The V8/Node headers for this exact version: MSI install, then node-gyp cache."""
    candidates = [node.parent / "include" / "node"]
    for variable in ("LOCALAPPDATA", "USERPROFILE"):
        base = environment.get(variable, "")
        if base:
            candidates.append(Path(base) / "node-gyp" / "Cache" / NODE_VERSION / "include" / "node")
            candidates.append(Path(base) / ".cache" / "node-gyp" / NODE_VERSION / "include" / "node")
    for headers in candidates:
        if all((headers / name).is_file() for name in ("node.h", "v8.h", "v8-internal.h", "v8-extension.h")):
            return headers
    return None


def _prepare_windows(node: Path, workspace: Path, environment: dict) -> tuple[Path, str] | None:
    try:
        if digest(node) != NODE_DIGESTS["win32"]:
            return None
        root = workspace.resolve()
        search_path = environment.get("PATH", "")
        tools: dict[str, Path] = {}
        for name in WINDOWS_TOOLS:
            found = shutil.which(name, path=search_path)
            if not found:
                return None
            tool = Path(found).resolve(strict=True)
            if root in tool.parents:
                return None
            tools[name] = tool
        headers = _windows_headers(node, environment)
        if headers is None or root in headers.resolve().parents:
            return None
        source = Path(__file__).with_suffix(".cc")
        header_files = sorted(headers.glob("*.h"))
        if not header_files:
            return None
        flags = ["/nologo", "/std:c++20", "/O2", "/EHsc", "/LD", "/DBUILDING_NODE_EXTENSION",
                 "/DUSING_V8_SHARED", "/DUSING_UV_SHARED", "/DNOMINMAX", "/DWIN32_LEAN_AND_MEAN"]
        identity = {"source": digest(source), "node": NODE_DIGESTS["win32"],
                    "tools": {name: digest(tool) for name, tool in tools.items()},
                    "headers": [(path.name, digest(path)) for path in header_files], "flags": flags}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        base = environment.get("LOCALAPPDATA") or tempfile.gettempdir()
        directory = Path(base) / "click-node-state" / key[:32]
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
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
                 if name not in {"NODE_OPTIONS", "NODE_PATH", "CLICK_NODE_OBSERVER_DIRECTORY", "CL", "_CL_", "LINK", "_LINK_"}}
        with tempfile.TemporaryDirectory(prefix="build-", dir=directory) as temporary:
            build = Path(temporary)
            # node.exe exports the V8 and Node API an addon links against;
            # an import library is generated from that export table instead
            # of fetching one, so the build stays offline and content-bound.
            exports = subprocess.run([str(tools["dumpbin"]), "/NOLOGO", "/EXPORTS", str(node)],
                                     cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120, text=True,
                                     encoding="utf-8", errors="replace")
            names = []
            for line in exports.stdout.splitlines():
                match = _EXPORT_LINE.match(line)
                if match and match.group(1) not in {"name", "RVA"}:
                    names.append(match.group(1))
            if exports.returncode or len(names) < 100:
                return None
            definition = build / "node.def"
            definition.write_text("LIBRARY node.exe\nEXPORTS\n" + "\n".join(names) + "\n", encoding="utf-8")
            library = build / "node.lib"
            made = subprocess.run([str(tools["lib"]), "/NOLOGO", f"/DEF:{definition}", "/MACHINE:X64", f"/OUT:{library}"],
                                  cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            if made.returncode or not library.is_file():
                return None
            output = build / "state.node"
            built = subprocess.run([str(tools["cl"]), *flags, f"/I{headers}", str(source), f"/Fe:{output}",
                                    "/link", str(library)],
                                   cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            if built.returncode or not output.is_file():
                return None
            probe = "const s=require(process.argv[1]);const a=s.randomState(Math.random);const r=Math.random();const b=s.randomState(Math.random);if(!a.startsWith('v8-xorshift128-cache64-v1:0:0000000000000000:0000000000000000:')||r!==0.9044192244068718||!b.startsWith('v8-xorshift128-cache64-v1:63:'))process.exitCode=1;"
            checked = subprocess.run([str(node), "--random-seed=12345", "-e", probe, str(output)],
                                     cwd=temporary, env=clean, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if checked.returncode or digest(node) != NODE_DIGESTS["win32"] or digest(source) != identity["source"]:
                return None
            artifact_digest = digest(output)
            metadata = build / "state.json"
            metadata.write_text(json.dumps({"build": key, "artifact": artifact_digest}))
            os.replace(output, artifact)
            os.replace(metadata, manifest)
            return artifact, artifact_digest
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
