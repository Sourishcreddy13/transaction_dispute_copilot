from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
# See README "Observability": the full `arize-phoenix` package (needed for
# scripts/export_traces.py's `phoenix.client.Client` -- the main project venv
# only ever has the lightweight `arize-phoenix-otel`, which doesn't provide a
# query client at all) deliberately lives only in this separate venv, never
# in the project's own, because it requires mcp>=2 which conflicts with this
# project's real MCP client (mcp>=1.27.2,<2).
_BIN_DIR = "Scripts" if os.name == "nt" else "bin"
_PY_NAME = "python.exe" if os.name == "nt" else "python"
PHOENIX_VENV_PYTHON = ROOT / ".venv-phoenix" / _BIN_DIR / _PY_NAME


def run(*args: str, python: str | Path = PYTHON, check: bool = True) -> "subprocess.CompletedProcess[bytes]":
    command = [str(python), *args]
    print("$", " ".join(command))
    return subprocess.run(command, cwd=ROOT, check=check)


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    have_phoenix_venv = PHOENIX_VENV_PYTHON.exists()
    # Only require a real Phoenix export once a `.venv-phoenix` has actually
    # been set up (per README "Observability"). Forcing this unconditionally
    # made the single-command pipeline hard-fail on every run regardless of
    # whether a Phoenix server was reachable, because scripts/export_traces.py
    # can never import a real Phoenix query client from the main venv at all
    # -- that's a Python import-path problem, not a "is the server up" one.
    os.environ.setdefault("REQUIRE_PHOENIX_EVIDENCE", "1" if have_phoenix_venv else "0")

    run("scripts/seed_data.py")
    run("scripts/test_memory_persistence.py")
    run("scripts/smoke_mcp.py")
    run("-m", "app.eval.run_eval")
    run("-m", "app.eval.deepeval_suite")
    run("scripts/run_failure_scenarios.py")

    if have_phoenix_venv:
        # Run specifically with .venv-phoenix's interpreter, which has the
        # full `arize-phoenix` package installed -- see the comment above.
        run("scripts/export_traces.py", python=PHOENIX_VENV_PYTHON)
    else:
        print(
            "No .venv-phoenix found -- exporting local diagnostic spans instead "
            "of live Phoenix traces. See README 'Observability' to enable a "
            "real export."
        )
        run("scripts/export_traces.py")

    run("scripts/build_golden_signals.py")
    run("scripts/build_dashboard.py")

    # A live screenshot needs both a reachable Phoenix server *and* Chromium
    # (`playwright install chromium`), neither of which this pipeline can
    # assume. build_dashboard.py (just above) already wrote a real,
    # matplotlib-rendered reports/dashboard.png from local logs, so a failure
    # here is never fatal to the run -- it just means that fallback image is
    # what ships instead of a live screenshot.
    dashboard_result = run("scripts/capture_phoenix_dashboard.py", check=False)
    if dashboard_result.returncode != 0:
        print(
            "Live Phoenix UI screenshot unavailable (no server reachable at "
            "http://127.0.0.1:6006, or Chromium isn't installed) -- keeping "
            "the matplotlib-rendered reports/dashboard.png from "
            "build_dashboard.py instead. See README 'Observability' to "
            "capture a live screenshot."
        )

    run("scripts/validate_evidence.py", "--strict")
    print("Evidence generation completed.")


if __name__ == "__main__":
    main()
