#!/usr/bin/env python3
"""Raw-concat PHONE_MODULE_JF_CHATFLOW fragments into the importable DSL file."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT = BASE_DIR / "PHONE_MODULE_JF_CHATFLOW.yml"
PART_RE = re.compile(r"PHONE_MODULE_JF_CHATFLOW_(\d+)\.yml$")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def discover_parts() -> list[Path]:
    numbered: list[tuple[int, Path]] = []
    for path in BASE_DIR.glob("PHONE_MODULE_JF_CHATFLOW_*.yml"):
        match = PART_RE.match(path.name)
        if match:
            numbered.append((int(match.group(1)), path))
    if not numbered:
        fail("no PHONE_MODULE_JF_CHATFLOW_*.yml fragments found")
    numbered.sort(key=lambda item: (item[0], item[1].name))
    numbers = [number for number, _ in numbered]
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    if numbers != expected:
        fail(f"fragment numbering must be continuous; found {numbers}, expected {expected}")
    return [path for _, path in numbered]


def merged_text(parts: list[Path]) -> str:
    chunks: list[str] = []
    for part in parts:
        text = part.read_text(encoding="utf-8")
        if not text:
            fail(f"fragment is empty: {part.name}")
        chunks.append(text)
    merged = "".join(chunks)
    if not merged.strip():
        fail("merged output is empty")
    try:
        json.loads(merged)
    except json.JSONDecodeError as exc:
        fail(f"merged DSL is not parseable JSON/YAML subset: {exc}")
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify output matches raw-concatenated fragments without writing")
    args = parser.parse_args(argv)

    parts = discover_parts()
    merged = merged_text(parts)

    if args.check:
        if not OUTPUT.exists():
            fail(f"output file missing: {OUTPUT}")
        current = OUTPUT.read_text(encoding="utf-8")
        if current != merged:
            fail(f"{OUTPUT.name} differs from raw-concatenated fragments")
        print(f"OK: {OUTPUT.name} matches {len(parts)} fragments")
        return 0

    OUTPUT.write_text(merged, encoding="utf-8")
    print(f"OK: wrote {OUTPUT} from {len(parts)} fragments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
