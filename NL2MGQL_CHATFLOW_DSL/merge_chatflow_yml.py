#!/usr/bin/env python3
"""Merge Dify Chatflow DSL YAML fragments by filename order.

The script keeps every fragment as raw text and does not parse YAML, so the
original formatting, indentation, variables, and block scalars remain unchanged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


OUTPUT_FILE_NAME = "NL2MGQL_CHATFLOW_FULL.yml"
EXPECTED_DIR_NAME = "NL2MGQL_CHATFLOW_DSL"
EXCLUDED_OUTPUT_NAMES = {
    "NL2MGQL_CHATFLOW_FULL.yml",
    "NL2MGQL_CHATFLOW_FULL.yaml",
}
EXCLUDED_EDGE_WORDS = ("merged", "full", "backup", "bak", "tmp")
YAML_SUFFIXES = {".yml", ".yaml"}


def natural_sort_key(path: Path) -> list[object]:
    """Return a deterministic natural filename sort key."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def is_candidate_fragment(path: Path, output_path: Path) -> bool:
    """Return True when path is a mergeable YAML fragment file."""
    if not path.is_file():
        return False

    if path.name.startswith("."):
        return False

    if path.suffix.lower() not in YAML_SUFFIXES:
        return False

    if path.resolve() == output_path.resolve():
        return False

    if path.name in EXCLUDED_OUTPUT_NAMES:
        return False

    stem = path.stem.lower()
    if any(stem.startswith(word) or stem.endswith(word) for word in EXCLUDED_EDGE_WORDS):
        return False

    # Manifest files describe fragment assembly and are not DSL fragments.
    if stem.endswith(".manifest") or stem == "manifest":
        return False

    return True


def collect_fragment_files(base_dir: Path, output_path: Path) -> list[Path]:
    """Collect mergeable YAML fragment files from base_dir in filename order."""
    files = [
        path
        for path in base_dir.iterdir()
        if is_candidate_fragment(path, output_path)
    ]
    return sorted(files, key=natural_sort_key)


def read_fragment(path: Path) -> str:
    """Read a fragment as UTF-8 text and normalize line endings to LF."""
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except OSError as exc:
        raise RuntimeError(f"failed to read fragment file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"failed to decode fragment file as UTF-8: {path}") from exc


def merge_files(files: list[Path], output_path: Path) -> None:
    """Merge raw fragment text into output_path, separated by one LF."""
    chunks: list[str] = []
    for path in files:
        content = read_fragment(path)
        chunks.append(content)
        print(f"[INFO] fragment: {path.name} ({path.stat().st_size} bytes)")

    merged = "\n".join(chunks)
    try:
        output_path.write_text(merged, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RuntimeError(f"failed to write output file: {output_path}") from exc


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    output_path = base_dir / OUTPUT_FILE_NAME

    print(f"[INFO] merge dir: {base_dir}")
    print(f"[INFO] output: {output_path}")

    if base_dir.name != EXPECTED_DIR_NAME:
        print(
            f"[WARNING] current directory name is {base_dir.name!r}, "
            f"expected {EXPECTED_DIR_NAME!r}"
        )

    files = collect_fragment_files(base_dir, output_path)
    if not files:
        raise RuntimeError(f"no mergeable yml/yaml fragment files found in: {base_dir}")

    if len(files) == 1:
        print("[WARNING] only 1 fragment file found; merging it as a complete output")

    print("[INFO] input files:")
    for path in files:
        print(f"  {path.name}")

    merge_files(files, output_path)
    print(f"[INFO] merged {len(files)} files into {output_path.name}")
    print(f"[INFO] output size: {output_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
