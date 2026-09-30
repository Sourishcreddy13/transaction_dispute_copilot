"""Streamlit front-end for the Transaction Dispute & Fraud-Triage Copilot.

Three tabs, each a thin UI over code that already exists in this repo --
this file adds no new business logic, it only calls it:

  1. Submit Dispute   -> app.workflow.Copilot.run()          (the real graph)
  2. Evidence Dashboard -> reads the committed reports/docs/logs artifacts
  3. Human Review Queue -> app.core.db.DB review_tasks + Copilot.resume_review()

Run with:
    streamlit run streamlit_app.py

Needs the same environment as the CLI (.env with GOOGLE_API_KEY / GROQ_API_KEY /
ACCESS_SECRET, etc. -- see README.md "Quickstart"). Uses the same
runtime/copilot.db and runtime/checkpoints.db as app/cli.py, so cases opened
here are visible to the CLI and vice versa.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import hmac
import os

import pandas as pd
import streamlit as st

from app.models import Role
from app.workflow import Copilot

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="Dispute & Fraud-Triage Copilot", layout="wide")




def require_streamlit_auth() -> tuple[str, Role]:
    expected = os.getenv("STREAMLIT_ACCESS_TOKEN", "")
    actor = os.getenv("STREAMLIT_ACTOR_ID", "analyst:A-001")
    role = Role(os.getenv("STREAMLIT_ROLE", "analyst"))
    if not expected or len(expected) < 20:
        st.error("STREAMLIT_ACCESS_TOKEN must be configured with a non-trivial development credential.")
        st.stop()
    provided = st.text_input("Access token", type="password", key="streamlit-access-token")
    if not provided or not hmac.compare_digest(provided, expected):
        st.info("Authenticate to access the local operations UI.")
        st.stop()
    return actor, role

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


@st.cache_resource
def get_copilot() -> Copilot:
    """One Copilot (= one Services/DB/graph) per Streamlit server process.
    Cheap to construct is not the point -- the point is that every tab in
    every browser tab talks to the SAME sqlite-backed case/review state,
    exactly like the CLI does."""
    return Copilot()


def run_async(coro):
    """Streamlit's script-rerun model has no persistent event loop, so each
    call gets its own via asyncio.run -- the same pattern app/cli.py effectively
    uses through Copilot.run()'s asyncio.run(self.run_async(...))."""
    return asyncio.run(coro)


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_jsonl_tail(path: Path, n: int = 20) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


actor, streamlit_role = require_streamlit_auth()
cop = get_copilot()

tab_intake, tab_evidence, tab_review = st.tabs(
    ["\U0001F4DD Submit Dispute", "\U0001F4CA Evidence & Observability", "\U0001F9D1‍⚖️ Human Review Queue"]
)


# --------------------------------------------------------------------------
# Tab 1: Dispute intake
# --------------------------------------------------------------------------

with tab_intake:
    st.header("Submit a dispute")
    st.caption(
        "This calls the real LangGraph copilot end to end (classification -> "
        "fraud scoring -> chargeback-rules retrieval -> decision) -- the same "
        "code path `app/cli.py run` uses."
    )

    col_case, col_form = st.columns([1, 2])

    with col_case:
        st.subheader("Case")
        st.caption(f"Authenticated actor: `{actor}` ({streamlit_role.value})")
        customer_options = [p["customer_id"] for p in cop.s.data.list_customers()]
        if streamlit_role == Role.customer:
            customer_options = [cop.s.principal(actor, streamlit_role).customer_scope]
        customer_id = st.selectbox("Customer", customer_options, index=0)

        existing_cases = cop.db.cases_for_customer(customer_id)
        case_options = ["<open a new case>"] + [
            f"{c['case_id']} ({c['state']})" for c in existing_cases
        ]
        chosen = st.selectbox("Case", case_options, index=0)

        if chosen == "<open a new case>":
            if st.button("Open new case", use_container_width=True):
                try:
                    principal = cop.s.principal(actor, Role.analyst)
                    cop.s.authorize_case_creation(principal, customer_id)
                    new_case_id = cop.db.create_case(customer_id, actor)
                    st.session_state["active_case_id"] = new_case_id
                    st.success(f"Opened {new_case_id}")
                    st.rerun()
                except PermissionError as exc:
                    st.error(f"Could not open case: {exc}")
        else:
            st.session_state["active_case_id"] = chosen.split(" ")[0]

        active_case_id = st.session_state.get("active_case_id")
        if active_case_id:
            st.info(f"Active case: **{active_case_id}**")
            case_row = cop.db.get_case(active_case_id)
            if case_row:
                st.json(
                    {
                        "state": case_row["state"],
                        "review_state": case_row["review_state"],
                        "version": case_row["version"],
                    },
                    expanded=False,
                )

    with col_form:
        st.subheader("Dispute message")
        dispute_text = st.text_area(
            "What the customer said",
            value="I did not make this transaction and want to dispute it.",
            height=120,
        )
        c1, c2 = st.columns(2)
        with c1:
            claimed_txn = st.text_input("Claimed transaction ID (optional)")
            claimed_amount = st.text_input("Claimed amount (optional)")
        with c2:
            claimed_merchant = st.text_input("Claimed merchant (optional)")
            claimed_date = st.text_input("Claimed date (optional)")

        run_clicked = st.button("Run copilot", type="primary", disabled=not active_case_id)

    if run_clicked and active_case_id:
        from app.models import DisputeClaim

        claim = DisputeClaim(
            dispute_message=dispute_text,
            claimed_transaction_id=claimed_txn or None,
            claimed_amount=claimed_amount or None,
            claimed_merchant=claimed_merchant or None,
            claimed_date=claimed_date or None,
        )
        with st.spinner("Running the graph (classification -> fraud -> policy -> decision)..."):
            try:
                response = run_async(
                    cop.run_async(active_case_id, actor, dispute_text, Role.analyst, claim=claim)
                )
                st.session_state["last_response"] = response.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001 - surface any failure to the operator, don't hide it
                st.exception(exc)

    if st.session_state.get("last_response"):
        resp = st.session_state["last_response"]
        st.divider()
        st.subheader("Result")

        badge_col, action_col = st.columns(2)
        badge_col.metric("Case state", resp["case_state"])
        rec = resp.get("recommendation")
        if rec:
            action_col.metric("Recommended action", rec["primary_action"])

        if resp.get("clarification"):
            st.warning(" / ".join(resp["clarification"]))

        if resp.get("customer_view"):
            st.markdown("**Customer-facing message**")
            st.info(resp["customer_view"])

        if resp.get("review_task"):
            st.markdown(
                f"**Escalated to human review** -- task `{resp['review_task']['task_id']}` "
                "(see the Human Review Queue tab)."
            )

        analyst_view = resp.get("analyst_view") or {}
        if analyst_view.get("warnings"):
            st.error(f"Operational warnings: {analyst_view['warnings']}")

        with st.expander("Analyst view (classification / fraud / policy / escalation)", expanded=True):
            st.json(analyst_view)

        if resp.get("provenance"):
            with st.expander("Decision provenance"):
                st.json(resp["provenance"])


# --------------------------------------------------------------------------
# Tab 2: Evidence & observability dashboard
# --------------------------------------------------------------------------

with tab_evidence:
    st.header("Evidence & observability")
    st.caption(
        "Renders the committed hackathon evidence artifacts directly -- "
        "nothing here is recomputed, it's the same files the review is scored from."
    )

    golden = load_json(ROOT / "reports" / "golden_signals.json")
    if golden:
        st.subheader("Golden signals")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Runs", golden.get("runs"))
        m2.metric("Total tokens", golden.get("tokens", {}).get("total"))
        m3.metric("Est. cost (USD)", f"{golden.get('cost', {}).get('estimated_usd', 0):.8f}")
        fallback_rate = golden.get("provider", {}).get("fallback_rate")
        m4.metric("Fallback rate", f"{fallback_rate:.0%}" if fallback_rate is not None else "n/a")

        latency = golden.get("latency_ms", {})
        rows = []
        for kind, pct in latency.items():
            if isinstance(pct, dict):
                rows.append({"span_kind": kind, **pct})
        if rows:
            st.markdown("**Latency by span kind (ms)**")
            df = pd.DataFrame(rows).set_index("span_kind")
            st.bar_chart(df[["p50", "p95"]])
            st.dataframe(df, use_container_width=True)

        qual = golden.get("qualitative_eval", {})
        if qual:
            st.markdown("**Qualitative eval (LLM-as-judge)**")
            qc1, qc2, qc3 = st.columns(3)
            qc1.metric("Answer Relevancy", qual.get("Answer Relevancy"))
            qc2.metric("Faithfulness", qual.get("Faithfulness"))
            qc3.metric("Hallucination", qual.get("Hallucination"))

        with st.expander("Raw golden_signals.json"):
            st.json(golden)
    else:
        st.warning("reports/golden_signals.json not found -- run `python scripts/build_golden_signals.py`.")

    st.divider()
    st.subheader("Cost / latency dashboard")
    dash_png = ROOT / "reports" / "dashboard.png"
    dash_csv = ROOT / "reports" / "dashboard_data.csv"
    if dash_png.exists():
        st.image(str(dash_png), caption="reports/dashboard.png")
    if dash_csv.exists():
        with st.expander("Underlying data (reports/dashboard_data.csv)"):
            st.dataframe(pd.read_csv(dash_csv), use_container_width=True, height=300)

    st.divider()
    st.subheader("Agent evaluation")
    eval_report = load_json(ROOT / "reports" / "eval_report.json")
    if eval_report:
        deterministic = eval_report.get("deterministic_evaluation", {})
        e1, e2, e3 = st.columns(3)
        e1.metric("Action accuracy", deterministic.get("action_accuracy"))
        e2.metric("Review accuracy", deterministic.get("review_accuracy"))
        e3.metric("Intent accuracy", deterministic.get("intent_accuracy"))
        with st.expander("Raw eval_report.json"):
            st.json(eval_report)

    st.divider()
    st.subheader("Failure-mode analysis")
    failure_md = ROOT / "docs" / "failure-analysis.md"
    if failure_md.exists():
        st.markdown(failure_md.read_text(encoding="utf-8"))

    st.divider()
    st.subheader("Governance pack")
    gov_docs = {
        "Risk register": "docs/risk-register.md",
        "Control catalog": "docs/controls.md",
        "Model / system card": "docs/model-card.md",
        "Compliance mapping": "docs/compliance.md",
        "Output-risk classification": "docs/output-risk.md",
    }
    gov_cols = st.columns(len(gov_docs))
    for col, (label, rel) in zip(gov_cols, gov_docs.items()):
        p = ROOT / rel
        with col:
            st.caption(label)
            if p.exists() and st.button(f"View {label}", key=f"gov-{rel}"):
                st.session_state["gov_doc"] = rel
    if st.session_state.get("gov_doc"):
        st.markdown((ROOT / st.session_state["gov_doc"]).read_text(encoding="utf-8"))

    st.divider()
    st.subheader("Recent audit trail (logs/agent_actions.jsonl)")
    actions = load_jsonl_tail(ROOT / "logs" / "agent_actions.jsonl", n=25)
    if actions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "timestamp": a.get("timestamp"),
                        "case_id": a.get("case_id"),
                        "action": a.get("action"),
                        "decision": a.get("decision"),
                        "event_hash": (a.get("event_hash") or "")[:12],
                    }
                    for a in actions
                ]
            ),
            use_container_width=True,
        )
    else:
        st.caption("No audit records yet -- run a dispute from the Submit Dispute tab.")


# --------------------------------------------------------------------------
# Tab 3: Human review queue
# --------------------------------------------------------------------------

with tab_review:
    st.header("Human review queue")
    st.caption(
        "High-value / high-fraud-score cases the graph routed to human_review "
        "(AC-03) sit here as review_tasks until a reviewer resolves them, then "
        "the graph resumes from its checkpoint via Copilot.resume_review()."
    )

    with cop.db.conn() as c:
        pending_rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM review_tasks WHERE status IN ('PENDING','CLAIMED') ORDER BY created_at DESC"
            )
        ]

    if not pending_rows:
        st.info("No pending review tasks. Submit a high-value/high-fraud-score dispute to generate one.")
    else:
        options = [f"{r['task_id']} -- case {r['case_id']} ({r['status']})" for r in pending_rows]
        picked = st.selectbox("Review task", options)
        picked_row = pending_rows[options.index(picked)]
        task_id = picked_row["task_id"]
        case_id = picked_row["case_id"]

        case_row = cop.db.get_case(case_id)
        st.json(
            {
                "task_id": task_id,
                "case_id": case_id,
                "status": picked_row["status"],
                "version": picked_row["version"],
                "case_state": case_row["state"] if case_row else None,
            },
            expanded=True,
        )

        # Pull the underlying recommendation out of the audit trail -- the
        # review_tasks row itself only stores the recommendation_id, not the
        # full recommendation payload.
        recommendation_payload = None
        for event in reversed(cop.db.get_audit(case_id)):
            if event["stage"] == "recommendation":
                payload = json.loads(event["payload_json"])
                if payload.get("recommendation", {}).get("recommendation_id") == picked_row["recommendation_id"]:
                    recommendation_payload = payload["recommendation"]
                    break
        if recommendation_payload:
            with st.expander("Recommendation under review", expanded=True):
                st.json(recommendation_payload)

        reviewer = actor if streamlit_role == Role.reviewer else ""
        if streamlit_role != Role.reviewer:
            st.warning("The review queue requires a reviewer-authenticated Streamlit session.")
            st.stop()

        col_claim, col_resolve = st.columns(2)

        with col_claim:
            if picked_row["status"] == "PENDING" and st.button("Claim this task"):
                claimed = cop.db.claim_review(task_id, reviewer, picked_row["version"])
                if claimed:
                    st.success("Claimed.")
                    st.rerun()
                else:
                    st.error("Could not claim -- someone else may already hold it.")

        with col_resolve:
            if picked_row["status"] == "CLAIMED":
                action = st.selectbox(
                    "Decision", ["provisional_credit", "chargeback", "investigate", "deny"]
                )
                reason_code = st.text_input("Reason code", value="EVIDENCE_SUFFICIENT")
                note = st.text_area("Note", value="", height=68)
                if st.button("Resolve and resume graph", type="primary"):
                    fresh = cop.db.get_review(task_id)
                    resolved = cop.db.resolve_review(
                        task_id, reviewer, fresh["version"], case_id, action, reason_code, note
                    )
                    if not resolved:
                        st.error(
                            "Could not resolve -- version conflict, or this reviewer created the "
                            "case/recommendation (self-review is blocked by design)."
                        )
                    else:
                        with st.spinner("Resuming the graph from its checkpoint..."):
                            try:
                                result = run_async(cop.resume_review(task_id))
                                st.success("Graph resumed and completed.")
                                st.json(
                                    {
                                        "case_state": result.get("case_state"),
                                        "final_disposition": (
                                            result["disposition"].model_dump(mode="json")
                                            if result.get("disposition")
                                            else None
                                        ),
                                    }
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.exception(exc)
                        st.rerun()

    st.divider()
    st.subheader("All open/queued cases")
    queue = cop.db.cases_for_queue()
    if queue:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "case_id": r["case_id"],
                        "customer_id": r["customer_id"],
                        "state": r["state"],
                        "review_state": r["review_state"],
                    }
                    for r in queue
                ]
            ),
            use_container_width=True,
        )
    else:
        st.caption("Nothing in the queue right now.")
