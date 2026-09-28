from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
_BIN_DIR = "Scripts" if os.name == "nt" else "bin"
_PY_NAME = "python.exe" if os.name == "nt" else "python"
PHOENIX_VENV_PYTHON = ROOT / ".venv-phoenix" / _BIN_DIR / _PY_NAME

GENERATED_LOGS = (
    "agent_actions.jsonl",
    "mcp_transcript.jsonl",
    "memory_test.log",
    "model_provider.jsonl",
    "runtime_spans.jsonl",
    "tool_calls.jsonl",
)
GENERATED_REPORTS = (
    "eval_report.json",
    "deepeval_qualitative.json",
    "failure-scenarios.json",
    "golden_signals.json",
    "dashboard_data.csv",
    "dashboard_data.csv.meta.json",
    "dashboard_summary.png",
    "dashboard.png",
    "dashboard.png.capture-meta.json",
    "evidence_run_meta.json",
)


def run(*args: str, python: str | Path = PYTHON, check: bool = True) -> subprocess.CompletedProcess:
    command = [str(python), *args]
    print("$", " ".join(command))
    return subprocess.run(command, cwd=ROOT, check=check)


def clear_generated_artifacts() -> None:
    for name in GENERATED_LOGS:
        (ROOT / "logs" / name).unlink(missing_ok=True)
    for name in GENERATED_REPORTS:
        (ROOT / "reports" / name).unlink(missing_ok=True)
    for name in ("phoenix_spans.jsonl", "phoenix_spans.jsonl.meta.json"):
        (ROOT / "traces" / name).unlink(missing_ok=True)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    if not PHOENIX_VENV_PYTHON.exists():
        raise RuntimeError(
            "Phoenix evidence requires .venv-phoenix. Create it with:\n"
            "  python3.12 -m venv .venv-phoenix && .venv-phoenix/bin/pip install arize-phoenix"
        )

    clear_generated_artifacts()

    evidence_id = "EVIDENCE-" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]
    # Small safety margin handles local clock/collector ingestion skew at the start boundary.
    evidence_start = datetime.now(timezone.utc) - timedelta(seconds=3)
    os.environ["REQUIRE_PHOENIX_EVIDENCE"] = "1"
    os.environ["EVIDENCE_ID"] = evidence_id
    os.environ["EVIDENCE_START_UTC"] = evidence_start.isoformat()

    run_meta = {
        "evidence_id": evidence_id,
        "started_at_utc": evidence_start.isoformat(),
        "project": os.getenv("PHOENIX_PROJECT_NAME", "transaction-dispute-copilot"),
    }
    (ROOT / "reports" / "evidence_run_meta.json").write_text(
        json.dumps(run_meta, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Evidence run: {evidence_id}")
    print(f"Evidence start: {evidence_start.isoformat()}")

    run("scripts/seed_data.py")
    run("scripts/test_memory_persistence.py")
    run("scripts/smoke_mcp.py")
    run("-m", "app.eval.run_eval")
    run("-m", "app.eval.deepeval_suite")
    run("scripts/run_failure_scenarios.py")

    time.sleep(3)
    run("scripts/export_traces.py", python=PHOENIX_VENV_PYTHON)
    run("scripts/build_golden_signals.py")
    run("scripts/build_dashboard.py")
    run("scripts/capture_phoenix_dashboard.py")
    run("scripts/validate_evidence.py", "--strict")
    print("Evidence generation completed.")


if __name__ == "__main__":
    main()
