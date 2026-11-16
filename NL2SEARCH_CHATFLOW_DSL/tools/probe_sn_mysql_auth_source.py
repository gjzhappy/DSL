#!/usr/bin/env python3
"""Read-only lookup of the local Java contract for Dify's MySQL query endpoint."""
from pathlib import Path
import re

ROOT = Path(r"D:\workspace\Kirin-Simulation-Platform-Back-End")
MARKERS = ("querySimple", "mysql/query/simple", "datacenter/mysql",
           "getLoginUser", "User not login", "SaCheckLogin", "PreAuthorize",
           "SecurityUtils", "getSsoRequest", "permitAll")
if not ROOT.is_dir():
    print("SOURCE_ROOT=NOT_FOUND")
    raise SystemExit(2)
print("PROBE=READ_ONLY")
print("BACKEND_SOURCE=" + str(ROOT))
java = list(ROOT.rglob("*.java"))
print("JAVA_FILES=" + str(len(java)))
hits = []
for path in java:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    if any(t in source for t in ("querySimple", "mysql/query/simple", "User not login", "SnDataCenterMysqlQueryController")):
        hits.append((path, source))
print("ROUTE_RELATED_FILES=" + str(len(hits)))
for path, source in hits[:12]:
    print("FILE=" + str(path.relative_to(ROOT)))
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if any(marker in line for marker in MARKERS):
            cleaned = line.strip()
            if len(cleaned) > 180:
                cleaned = cleaned[:180]
            if re.search(r"(?i)password|secret|bearer|api.?key|private.?key", cleaned):
                cleaned = "[AUTH_CONFIG_REDACTED]"
            print("LINE=" + str(index + 1) + " " + cleaned)
if not hits:
    print("CONTRACT=NOT_IN_LOCAL_JAVA_SOURCES")
else:
    print("CONTRACT=SOURCE_HITS_FOUND_REVIEW_REQUIRED")
print("NO_NETWORK=YES")
print("NO_MUTATIONS=YES")
