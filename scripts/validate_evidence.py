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

    # Committed evidence must not contain developer-machine paths.
    absolute_path_pattern = re.compile(r"(?:/Users/|/home/|/mnt/data/|(?:^|[\s\"\'(])[A-Za-z]:[\\/])")
    for directory in (ROOT / "traces", ROOT / "reports", ROOT / "logs", ROOT / "docs"):
        for path in directory.rglob("*"):
            if not path.is_file() or path.name == ".gitkeep":
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if absolute_path_pattern.search(content):
                errors.append(f"NON_PORTABLE_EVIDENCE_PATH:{path.relative_to(ROOT)}")

    # Basic evidence content checks.
    if args.strict:
        try:
            report = json.loads((ROOT / "reports/eval_report.json").read_text())
            if "deterministic_evaluation" not in report:
                errors.append("EVAL_SCHEMA:missing deterministic_evaluation")
            for result in report.get("deterministic_evaluation", {}).get("results", []):
                citation = result.get("policy_citation")
                if isinstance(citation, str) and citation.startswith(("/", "\\")):
                    errors.append("EVAL_NON_PORTABLE_POLICY_CITATION")
        except Exception as exc:
            errors.append(f"EVAL_UNREADABLE:{exc}")


    if args.strict:
        trace_path = ROOT / "traces/phoenix_spans.jsonl"
        try:
            lines = [json.loads(x) for x in trace_path.read_text(encoding="utf-8").splitlines() if x.strip()]
            if not lines:
                errors.append("PHOENIX_EMPTY:traces/phoenix_spans.jsonl")
            elif not any(x.get("trace_id") or x.get("context.trace_id") for x in lines):
                errors.append("PHOENIX_NO_TRACE_ID:trace export lacks trace identity")
            trace_meta = trace_path.with_suffix(trace_path.suffix + ".meta.json")
            if not trace_meta.exists():
                errors.append("PHOENIX_NO_EXPORT_METADATA:missing trace export metadata")
            else:
                try:
                    meta = json.loads(trace_meta.read_text(encoding="utf-8"))
                    if meta.get("generated_by") != "scripts/export_traces.py" or meta.get("source") != "Arize Phoenix":
                        errors.append("PHOENIX_INVALID_EXPORT_METADATA")
                except Exception as exc:
                    errors.append(f"PHOENIX_EXPORT_METADATA_UNREADABLE:{exc}")
        except Exception as exc:
            errors.append(f"PHOENIX_UNREADABLE:{exc}")

        dashboard = ROOT / "reports/dashboard.png"
        marker = dashboard.with_suffix(".png.capture-meta.json")
        if not marker.exists():
            errors.append("PHOENIX_DASHBOARD_NO_CAPTURE_MARKER:run scripts/capture_phoenix_dashboard.py")
        data_marker = ROOT / "reports/dashboard_data.csv.meta.json"
        if not data_marker.exists():
            errors.append("PHOENIX_DASHBOARD_DATA_NO_METADATA")
        else:
            try:
                csv_meta = json.loads(data_marker.read_text(encoding="utf-8"))
                if csv_meta.get("source") != "traces/phoenix_spans.jsonl":
                    errors.append("PHOENIX_DASHBOARD_DATA_WRONG_SOURCE")
            except Exception as exc:
                errors.append(f"PHOENIX_DASHBOARD_DATA_METADATA_UNREADABLE:{exc}")

        def check_jsonl(rel: str, required: set[str]) -> None:
            path = ROOT / rel
            try:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except Exception as exc:
                errors.append(f"JSONL_UNREADABLE:{rel}:{exc}")
                return
            if not rows:
                errors.append(f"JSONL_EMPTY:{rel}")
                return
            for index, row in enumerate(rows, start=1):
                missing = sorted(key for key in required if key not in row)
                if missing:
                    errors.append(f"JSONL_SCHEMA:{rel}:line={index}:missing={','.join(missing)}")

        check_jsonl(
            "logs/tool_calls.jsonl",
            {"timestamp", "agent", "tool_name", "args", "result", "latency_ms", "status"},
        )
        check_jsonl(
            "logs/mcp_transcript.jsonl",
            {"timestamp", "agent", "tool_name", "args", "result", "latency_ms", "status"},
        )
        check_jsonl(
            "logs/agent_actions.jsonl",
            {"timestamp", "actor", "action", "tool", "decision", "case_id", "event_hash", "payload"},
        )

        memory_log = ROOT / "logs/memory_test.log"
        if not memory_log.exists() or "cross_session_recall=True" not in memory_log.read_text(encoding="utf-8"):
            errors.append("MEMORY_EVIDENCE_INVALID:cross_session_recall=True not proved")

        failure_report = ROOT / "reports/failure-scenarios.json"
        try:
            failures = json.loads(failure_report.read_text(encoding="utf-8"))
            if not isinstance(failures, list) or len(failures) < 3:
                errors.append("FAILURE_EVIDENCE_TOO_FEW")
        except Exception as exc:
            errors.append(f"FAILURE_EVIDENCE_UNREADABLE:{exc}")

        # Validate Phoenix-derived dashboard metrics and cost lineage.
        try:
            import csv as _csv
            import yaml as _yaml
            with (ROOT / "config/providers.yaml").open(encoding="utf-8") as _h:
                _cfg = _yaml.safe_load(_h) or {}
            _prices = {
                name: (
                    float(cfg.get("pricing", {}).get("input_usd_per_1m_tokens", 0.0)),
                    float(cfg.get("pricing", {}).get("output_usd_per_1m_tokens", 0.0)),
                )
                for name, cfg in _cfg.get("providers", {}).items()
            }
            with (ROOT / "reports/dashboard_data.csv").open(encoding="utf-8", newline="") as _h:
                _rows = list(_csv.DictReader(_h))
            _llm_rows = [r for r in _rows if r.get("provider") and int(float(r.get("total_tokens") or 0)) > 0]
            if not _llm_rows:
                errors.append("DASHBOARD_NO_LLM_TOKEN_ROWS")
            _csv_cost = 0.0
            _csv_input = 0
            _csv_output = 0
            for _r in _llm_rows:
                _p = _r.get("provider")
                _in = int(float(_r.get("input_tokens") or 0))
                _out = int(float(_r.get("output_tokens") or 0))
                _csv_input += _in
                _csv_output += _out
                _ip, _op = _prices.get(_p, (0.0, 0.0))
                _expected = _in / 1_000_000 * _ip + _out / 1_000_000 * _op
                _actual = float(_r.get("estimated_cost_usd") or 0.0)
                if abs(_actual - _expected) > 1e-8:
                    errors.append(f"DASHBOARD_COST_MISMATCH:provider={_p}:span={_r.get('span_id')}")
                _csv_cost += _expected
            _gold = json.loads((ROOT / "reports/golden_signals.json").read_text(encoding="utf-8"))
            _gold_tokens = _gold.get("tokens", {})
            if _gold_tokens.get("input") != _csv_input or _gold_tokens.get("output") != _csv_output:
                errors.append("GOLDEN_DASHBOARD_TOKEN_MISMATCH")
            _gold_latency = _gold.get("latency_ms", {})
            if not _gold_latency.get("thinking", {}).get("p50"):
                errors.append("GOLDEN_MISSING_THINKING_LATENCY")
            if not _gold.get("source_manifest_sha256") or not _gold.get("source_revision"):
                errors.append("GOLDEN_MISSING_SOURCE_PROVENANCE")
            _gold_cost = float(_gold.get("cost", {}).get("estimated_usd") or 0.0)
            if abs(_gold_cost - _csv_cost) > 1e-8:
                errors.append("GOLDEN_DASHBOARD_COST_MISMATCH")
            _capture_meta = json.loads((ROOT / "reports/dashboard.png.capture-meta.json").read_text(encoding="utf-8"))
            if _capture_meta.get("capture_type") != "phoenix_ui_project_page":
                errors.append("PHOENIX_DASHBOARD_NOT_PROJECT_PAGE")
        except Exception as exc:
            errors.append(f"DASHBOARD_EVIDENCE_UNREADABLE:{exc}")


        # Validate that every CTRL-* mitigation in the risk register resolves to the control catalog.
        try:
            import re as _re
            risk_text = (ROOT / "docs/risk-register.md").read_text(encoding="utf-8")
            controls_text = (ROOT / "docs/controls.md").read_text(encoding="utf-8")
            risk_controls = set(_re.findall(r"CTRL-[A-Z0-9-]+", risk_text))
            catalog_controls = set(_re.findall(r"`(CTRL-[A-Z0-9-]+)`", controls_text))
            missing_controls = sorted(risk_controls - catalog_controls)
            if missing_controls:
                errors.append("CONTROL_CITATIONS_UNRESOLVED:" + ",".join(missing_controls))
        except Exception as exc:
            errors.append(f"CONTROL_CITATION_VALIDATION_ERROR:{exc}")

        # Validate failure citations against real generated evidence.
        try:
            failures = json.loads((ROOT / "reports/failure-scenarios.json").read_text(encoding="utf-8"))
            trace_rows = [json.loads(line) for line in (ROOT / "traces/phoenix_spans.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            trace_pairs = {(r.get("context.trace_id") or r.get("trace_id"), r.get("context.span_id") or r.get("span_id")) for r in trace_rows}
            tool_lines = [json.loads(line) for line in (ROOT / "logs/tool_calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            for index, failure in enumerate(failures, 1):
                if failure.get("phoenix_ref"):
                    ref = failure["phoenix_ref"]
                    if (ref.get("trace_id"), ref.get("span_id")) not in trace_pairs:
                        errors.append(f"FAILURE_CITATION_UNRESOLVED:failure={index}")
                elif failure.get("tool_log_ref"):
                    ref = failure["tool_log_ref"]
                    line_no = int(ref.get("line", 0))
                    if line_no < 1 or line_no > len(tool_lines):
                        errors.append(f"FAILURE_TOOL_CITATION_UNRESOLVED:failure={index}")
                    else:
                        row = tool_lines[line_no - 1]
                        if row.get("tool_name") != ref.get("tool_name") or row.get("status") != "ERROR":
                            errors.append(f"FAILURE_TOOL_CITATION_MISMATCH:failure={index}")
                else:
                    errors.append(f"FAILURE_CITATION_MISSING:failure={index}")
        except Exception as exc:
            errors.append(f"FAILURE_CITATION_VALIDATION_ERROR:{exc}")

    result = {"ok": not errors, "strict": args.strict, "errors": errors}
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
