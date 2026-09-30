from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = (ROOT / "traces", ROOT / "reports", ROOT / "logs", ROOT / "docs")

# Evidence must move between developer machines without changing its semantic content.
# Normalize only known repository-root prefixes; never rewrite arbitrary URLs or data.
PREFIXES = [
    re.compile(r"/Users/[^\"'\\\n]+"),
    re.compile(r"/home/[^\"'\\\n]+"),
    re.compile(r"/mnt/data/[^\"'\\\n]+"),
    re.compile(r"[A-Za-z]:[\\/][^\"'\\\n]+"),
]


def normalize_text(text: str) -> str:
    out = text
    # Preserve semantic file citations by converting this checkout's absolute
    # root into repository-relative paths. Handle both POSIX and Windows forms.
    root_posix = str(ROOT.resolve()).replace("\\", "/").rstrip("/")
    root_native = str(ROOT.resolve()).rstrip("\\/")
    for prefix in (root_posix + "/", root_native + os.sep):
        out = out.replace(prefix, "")
    for pattern in PREFIXES:
        out = pattern.sub("[LOCAL_PATH_REDACTED]", out)
    return out


def main() -> None:
    changed = 0
    for directory in TARGET_DIRS:
        for path in directory.rglob("*"):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            normalized = normalize_text(raw)
            if normalized != raw:
                path.write_text(normalized, encoding="utf-8")
                changed += 1
    print(json.dumps({"normalized_files": changed}))


if __name__ == "__main__":
    main()
