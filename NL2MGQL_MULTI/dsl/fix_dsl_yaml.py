#!/usr/bin/env python3
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
BAD_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]')
NAME_RE = re.compile(r'NL2MGQL_MULTI_(\d+)\.yml$')

@dataclass
class Part:
    path: str
    index: int
    offset: int
    text: str


def list_parts(base_dir: str):
    paths = glob.glob(os.path.join(base_dir, 'NL2MGQL_MULTI_*.yml'))
    items = []
    for p in paths:
        m = NAME_RE.search(os.path.basename(p))
        if m:
            items.append((int(m.group(1)), p))
    items.sort()
    if not items:
        raise SystemExit('No NL2MGQL_MULTI_*.yml files found')
    parts = []
    off = 0
    for idx, p in items:
        t = open(p, 'r', encoding='utf-8', errors='surrogatepass').read()
        parts.append(Part(path=p, index=idx, offset=off, text=t))
        off += len(t)
    return parts


def report_bad_chars(full_text: str, parts):
    found = list(BAD_RE.finditer(full_text))
    if not found:
        print('No illegal control characters found before cleaning.')
        return
    print(f'Found {len(found)} illegal control characters:')
    for m in found:
        pos = m.start()
        ch = m.group(0)
        part = next(p for p in parts if p.offset <= pos < p.offset + len(p.text))
        rel = pos - part.offset
        line = part.text.count('\n', 0, rel) + 1
        col = rel - (part.text.rfind('\n', 0, rel) + 1) + 1
        ctx = part.text[max(0, rel - 20): min(len(part.text), rel + 20)].replace('\n', '\\n')
        print(f'- pos={pos}, file={os.path.basename(part.path)}, line={line}, column={col}, unicode=U+{ord(ch):04X}, context={ctx}')


def validate_yaml(full_text: str):
    try:
        import yaml  # type: ignore
        yaml.safe_load(full_text)
        print('YAML_OK (python yaml.safe_load)')
        return
    except ModuleNotFoundError:
        print('PyYAML not available, fallback to Ruby Psych validation...')
    except Exception as e:
        print(f'YAML_ERROR (python yaml.safe_load): {e}')
        raise

    cmd = [
        'ruby', '-e',
        "require 'yaml'; text=STDIN.read; YAML.safe_load(text); puts 'YAML_OK (ruby psych)'"
    ]
    proc = subprocess.run(cmd, input=full_text.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode('utf-8', errors='replace'))
        raise SystemExit(proc.returncode)
    sys.stdout.write(proc.stdout.decode('utf-8', errors='replace'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='.', help='directory containing NL2MGQL_MULTI_*.yml')
    ap.add_argument('--chunk-lines', type=int, default=1000)
    args = ap.parse_args()

    base_dir = os.path.abspath(args.dir)
    parts = list_parts(base_dir)
    full_text = ''.join(p.text for p in parts)

    report_bad_chars(full_text, parts)

    cleaned = ANSI_RE.sub('', full_text)
    cleaned = BAD_RE.sub('', cleaned)

    print('After cleaning:')
    if BAD_RE.search(cleaned):
        raise SystemExit('Still has illegal control chars after cleaning')
    print('- illegal control chars: 0')
    print(f'- U+009B count: {cleaned.count(chr(0x9B))}')

    validate_yaml(cleaned)

    backup_dir = os.path.join(base_dir, 'backup')
    os.makedirs(backup_dir, exist_ok=True)
    for p in parts:
        shutil.copy2(p.path, os.path.join(backup_dir, os.path.basename(p.path)))

    for old in glob.glob(os.path.join(base_dir, 'NL2MGQL_MULTI_*.yml')):
        os.remove(old)

    lines = cleaned.splitlines(keepends=True)
    total = 0
    for i in range(0, len(lines), args.chunk_lines):
        chunk_lines = lines[i:i + args.chunk_lines]
        out = os.path.join(base_dir, f'NL2MGQL_MULTI_{i // args.chunk_lines}.yml')
        with open(out, 'w', encoding='utf-8', newline='') as f:
            f.write(''.join(chunk_lines))
        lc = len(chunk_lines)
        total += lc
        print(f'Wrote {os.path.basename(out)}: {lc} lines')
    print(f'Total lines written: {total}')

if __name__ == '__main__':
    main()
