"""Explicit, authorized bootstrap CLI for candidate analysis.

Invoke through Click's existing mutation capability. Importing this module or
installing the package never imports a user project or changes its policy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import shutil

if __package__:
    from . import click_import_bootstrap
else:
    import click_import_bootstrap

(click_test_inventory, click_shard_proposal) = click_import_bootstrap.load_siblings(
    __package__, "click_test_inventory", "click_shard_proposal"
)


def save_analysis(value: dict, project: Path) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="click-analysis-"))
    if click_test_inventory.inside(project.resolve(), directory.resolve()):
        directory.rmdir()
        raise click_test_inventory.AnalysisError("temporary-directory-inside-project")
    try:
        with (directory / "analysis.json").open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        # Only our own newly-created artifact, never target-project files.
        (directory / "analysis.json").unlink(missing_ok=True)
        directory.rmdir()
        raise
    return directory


def save_proposal(value: dict, project: Path) -> Path:
    directory = save_analysis(value["analysis"], project)
    try:
        metadata = {key: item for key, item in value.items() if key not in ("analysis", "proposals")}
        with (directory / "proposal.json").open("x", encoding="utf-8") as stream:
            json.dump(metadata, stream, indent=2, sort_keys=True)
            stream.write("\n")
        for name in (".click/evidence-shards.json", ".click/evidence-dependencies.json"):
            if name not in value["proposals"]:
                continue
            target = directory / name
            target.parent.mkdir(exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                json.dump(value["proposals"][name], stream, indent=2, sort_keys=True)
                stream.write("\n")
    except BaseException:
        shutil.rmtree(directory)  # Exclusively this call's new private directory.
        raise
    return directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("analyze", "propose"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--cwd", type=Path)
    # Split the explicit parent argv without ever evaluating a shell string.
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    options = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1:]
    proposal = options.operation == "propose"
    operation = click_shard_proposal.propose if proposal else click_test_inventory.analyze
    value = operation(options.project, command, cwd=options.cwd)
    try:
        directory = (save_proposal if proposal else save_analysis)(value, options.project)
    except (OSError, ValueError):
        print(json.dumps({"status": "blocked", "reason": "artifact-storage-unavailable",
                          "authority": False, "reuse_ready": False}))
        return 2
    print(json.dumps({"status": value["status"], "reasons": value["reasons"],
                      "artifact_directory": str(directory), "authority": False,
                      "reuse_ready": False}))
    return 0 if value["status"] in ("analysis-complete", "proposal-ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
