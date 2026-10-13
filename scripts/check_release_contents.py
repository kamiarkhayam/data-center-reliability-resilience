"""Reject research artifacts in the Git index or reachable history.

This is a path-based release check, not a detector of numerical findings in prose.
"""

import argparse
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    ".runtime",
    ".packaging",
    ".venv",
    "paper",
    "outputs",
    "results",
    "reference_results",
    "plot_ready",
    "figs",
    "figures",
    "models",
    "checkpoints",
    "data_raw",
    "data_intermediate",
    "data_processed",
}
EXCLUDED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".eps",
    ".tif",
    ".tiff",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".ipynb",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".npy",
    ".npz",
    ".parquet",
    ".h5",
    ".hdf5",
    ".zip",
    ".gz",
    ".tar",
    ".bundle",
}
APPROVED_DATA_FILES = {
    "docs/provenance/numerical_literal_audit.csv",
    "docs/provenance/source_file_manifest.csv",
    "docs/provenance/revision_parameter_sources.json",
    "docs/provenance/revision_source_manifest.json",
    "docs/provenance/revision_regression.json",
    "src/dc_restoration/revision/resources/rts_fleet.csv",
    "src/dc_restoration/revision/resources/chronology_protocol.json",
    "src/dc_restoration/revision/resources/economics.json",
    "src/dc_restoration/revision/resources/facility.json",
    "src/dc_restoration/revision/resources/hurricane.json",
    "src/dc_restoration/revision/resources/hurricane_protocol.json",
    "src/dc_restoration/spatial/source_manifest.json",
}


def violations(paths):
    problems = []
    for name in sorted(set(paths)):
        path = PurePosixPath(name)
        parts = {part.lower() for part in path.parts[:-1]}
        suffix = path.suffix.lower()
        if parts & EXCLUDED_DIRECTORIES:
            reason = "research/artifact directory"
        elif name.startswith("data/") and name != "data/README.md":
            reason = "external study input"
        elif suffix in EXCLUDED_EXTENSIONS:
            reason = "rendered artifact, manuscript, array, checkpoint or archive"
        elif suffix in {".csv", ".tsv", ".json", ".jsonl"}:
            if name in APPROVED_DATA_FILES:
                continue
            if name.startswith("tests/fixtures/tiny_case/") and suffix == ".csv":
                continue
            reason = "unapproved data file; use configuration or an invented fixture"
        else:
            continue
        problems.append(f"{name}: {reason}")
    return problems


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-history",
        action="store_true",
        help="check file paths in every commit reachable from local refs",
    )
    args = parser.parse_args()
    if args.all_history:
        commits = git("rev-list", "--all").splitlines()
        paths = {
            name
            for commit in commits
            for name in git("ls-tree", "-r", "--name-only", "-z", commit).split("\0")
            if name
        }
    else:
        paths = {name for name in git("ls-files", "-z").split("\0") if name}
    problems = violations(paths)
    if problems:
        raise SystemExit("Code-only release check failed:\n" + "\n".join(problems))
    print(f"Code-only path check passed ({len(paths)} distinct paths).")


if __name__ == "__main__":
    main()
