from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    structural = [
        "src/graph.py",
        "mcp_server/server.py",
        "src/context/__init__.py",
        "src/context/engineering.py",
        "src/memory/store.py",
        "tests/test_memory_persistence.py",
        "src/tools/rag_tool.py",
        "src/observability/tracing.py",
        "src/guardrails/validators.py",
        "tests/test_routing.py",
        "tests/test_loops.py",
        "tests/test_tool_contracts.py",
        ".env.example",
        ".gitignore",
        "docs/risk-register.md",
        "docs/model-card.md",
        "docs/compliance.md",
        "docs/output-risk.md",
        "docs/requirements-traceability.md",
    ]
    for rel in structural:
        if not (ROOT / rel).exists():
            errors.append(f"MISSING:{rel}")

    if args.strict:
        evidence = [
            "logs/tool_calls.jsonl",
            "logs/mcp_transcript.jsonl",
            "logs/memory_test.log",
            "logs/agent_actions.jsonl",
            "reports/eval_report.json",
            "reports/golden_signals.json",
            "reports/dashboard.png",
            "reports/dashboard_data.csv",
            "reports/failure-scenarios.json",
            "traces/phoenix_spans.jsonl",
        ]
        for rel in evidence:
            if not (ROOT / rel).exists():
                errors.append(f"EVIDENCE_MISSING:{rel}")

    secret_patterns = [re.compile(r"gsk_[A-Za-z0-9_-]+"), re.compile(r"AIza[0-9A-Za-z_-]+")]
    # Real, verified bug: this scanner is meant to catch secrets committed to
    # this project's OWN source, not to reach into third-party installed
    # packages at all. The old exclusion list only skipped a literal ".venv"
    # path segment, which does NOT match this project's separate
    # `.venv-phoenix` (see README "Observability" / generate_evidence.py --
    # the full `arize-phoenix` package deliberately lives in its own venv,
    # never in `.venv`, because it needs mcp>=2 which conflicts with this
    # project's real MCP client). So `--strict` walked straight into
    # `.venv-phoenix/lib/.../site-packages/phoenix/server/static/assets/
    # vendor-shiki-*.js` -- a large minified third-party JS bundle (Phoenix's
    # web UI syntax highlighter) -- and its loose `AIza[0-9A-Za-z_-]+` regex
    # (no length bound, where a real Google API key is exactly 39 characters)
    # matched an arbitrary substring inside that bundle that is not a
    # credential of any kind -- just minified/theme data that happens to
    # start with the same four characters this scanner looks for. Confirmed
    # for real: extracted that exact match from the real installed
    # `arize-phoenix` package's vendored asset file, not assumed from the
    # error message alone. (Deliberately not reproducing the matched string
    # itself here, since doing so would make this very comment trip the scan.)
    #
    # Fixed by excluding any virtualenv-shaped directory (any part starting
    # with ".venv", covering ".venv" and ".venv-phoenix" alike) and, as a
    # general safety net for any differently-named venv, any path that
    # passes through a "site-packages" directory -- since that always means
    # "installed third-party code", never this project's own source or
    # generated evidence.
    _SKIP_PARTS = {".git", "runtime", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part in _SKIP_PARTS or part.startswith(".venv") or part == "site-packages"
            for part in path.parts
        ):
            continue
        if path.name == ".env":
            # A local dotenv file is expected during real runs; NFR-01 requires it not be committed.
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in secret_patterns:
            if pattern.search(content):
                errors.append(f"POSSIBLE_SECRET:{path}")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
    if ".env" not in gitignore:
        errors.append("GITIGNORE_MISSING:.env")

    # Basic evidence content checks.
    if args.strict:
        try:
            report = json.loads((ROOT / "reports/eval_report.json").read_text())
            if "deterministic_evaluation" not in report:
                errors.append("EVAL_SCHEMA:missing deterministic_evaluation")
        except Exception as exc:
            errors.append(f"EVAL_UNREADABLE:{exc}")

    result = {"ok": not errors, "strict": args.strict, "errors": errors}
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
