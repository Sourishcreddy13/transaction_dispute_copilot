from __future__ import annotations

import asyncio
import hashlib
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.models import CaseState, Classification, DecisionRecommendation, FinalDisposition, FraudResult, PolicyOutcome
from src.mcp_client import MCPToolTimeout
from src.context.engineering import ContextEngineer
from src.guardrails.validators import (
    sanitize_output,
    validate_customer_view,
    validate_release_invariants,
    validate_recommendation,
)


from src.execution.journey import ExecutionJourney, input_summary, output_summary


class CopilotState(TypedDict, total=False):
    case_id: str
    actor_id: str
    role: str
    masked_text: str
    claim_hints: dict[str, Any]
    quarantined: bool
    injection_flag: bool
    pii_entities: list[dict[str, Any]]
    conversation_context: list[str]
    memory_hits: list[dict[str, Any]]
    authorized: bool

    classification: Classification | None
    transaction_id: str | None
    transaction_candidates: list[dict[str, Any]]
    transaction: dict[str, Any] | None
    history: list[dict[str, Any]]
    profile: dict[str, Any] | None
    prior: list[dict[str, Any]]
    account_snapshot: dict[str, Any] | None
    policy_resource_seen: bool

    fraud: FraudResult | None
    policy_hits: list[dict[str, Any]]
    policy: PolicyOutcome | None
    recommendation: DecisionRecommendation | None
    review_task_id: str | None
    review_resume: dict[str, Any] | None
    disposition: dict[str, Any] | None

    case_state: str
    review_state: str
    audit_state: str
    route: str
    errors: list[str]
    run_id: str
    snapshot_id: str | None
    customer_view: str
    selected_transaction_source: str | None


class CopilotGraph:
    """Authoritative LangGraph execution path for dispute triage."""

    def __init__(self, services, checkpointer=None, journey: ExecutionJourney | None = None):
        self.s = services
        self.journey = journey
        self.checkpointer = checkpointer
        self.context: ContextEngineer = services.context

        self.builder = StateGraph(CopilotState)
        nodes = [
            ("case_authorize", self.case_authorize),
            ("ingress", self.ingress),
            ("supervisor", self.supervisor),
            ("classification_agent", self.classification_agent),
            ("case_data_agent", self.case_data_agent),
            ("fraud_scoring_agent", self.fraud_scoring_agent),
            ("chargeback_rules_agent", self.chargeback_rules_agent),
            ("decision_engine", self.decision_engine),
            ("human_review", self.human_review),
            ("disposition_commit", self.disposition_commit),
            ("finalize", self.finalize),
        ]
        for name, fn in nodes:
            if name == "human_review":
                wrapped = fn
            else:
                wrapped = self._wrap_node(name, fn)
            self.builder.add_node(name, wrapped)

        self.builder.add_edge(START, "case_authorize")
        self.builder.add_edge("case_authorize", "ingress")
        self.builder.add_edge("ingress", "supervisor")
        self.builder.add_conditional_edges(
            "supervisor",
            self.route,
            {
                "classification_agent": "classification_agent",
                "case_data_agent": "case_data_agent",
                "fraud_scoring_agent": "fraud_scoring_agent",
                "chargeback_rules_agent": "chargeback_rules_agent",
                "decision_engine": "decision_engine",
                "human_review": "human_review",
                "disposition_commit": "disposition_commit",
                "finalize": "finalize",
                "END": END,
            },
        )
        for node in (
            "classification_agent",
            "case_data_agent",
            "fraud_scoring_agent",
            "chargeback_rules_agent",
            "decision_engine",
            "human_review",
            "disposition_commit",
        ):
            self.builder.add_edge(node, "supervisor")
        self.builder.add_edge("finalize", END)
        self.graph = self.builder.compile(checkpointer=checkpointer)

    def _wrap_node(self, node_id: str, fn):
        async def wrapped(state: CopilotState):
            if self.journey:
                self.journey.start(node_id, input_summary(node_id, state))
            try:
                result = await fn(state)
                if self.journey:
                    if isinstance(result, dict) and result.get("errors"):
                        message = "; ".join(str(error) for error in result["errors"])
                        self.journey.fail(node_id, RuntimeError(message))
                    else:
                        self.journey.complete(node_id, output_summary(node_id, result or {}))
                return result
            except Exception as exc:
                if self.journey:
                    self.journey.fail(node_id, exc)
                raise

        return wrapped

    async def case_authorize(self, state: CopilotState):
        with self.s.telemetry.span("agent.case_authorize", case_id=state["case_id"], run_id=state["run_id"]):
            try:
                principal = self.s.principal(state["actor_id"], state["role"])
                self.s.authorize(principal, state["case_id"])
                return {"authorized": True}
            except PermissionError as exc:
                return {
                    "authorized": False,
                    "errors": [str(exc) or "AUTHZ_CASE_DENIED"],
                    "case_state": CaseState.NEEDS_INFO.value,
                    "route": "finalize",
                }

    async def ingress(self, state: CopilotState):
        with self.s.telemetry.span("agent.ingress", case_id=state["case_id"], run_id=state["run_id"]):
            if not state.get("authorized"):
                return {"route": "finalize"}
            try:
                self.s._transition(state["case_id"], CaseState.RUNNING.value, "NONE")
                env = self.context.isolate(state["masked_text"])
                self.context.write(env, {"case_id": state["case_id"], "role": state["role"]})
                self.context.select(env, {"case_id": state["case_id"], "role": state["role"]})
                self.context.compress(env)

                turn_id = self.s.db.next_turn_id(state["case_id"])
                previous = self.s.db.get_context(state["case_id"], limit=5)
                self.s.db.save_context(state["case_id"], turn_id, env.masked_text, env.conversation_summary)
                customer_id = self.s.db.get_case(state["case_id"])["customer_id"]
                memory_hits = self.s.memory.recall(customer_id, minimum=self.s.memory.customer_confirmed_level)
                historical_memory = [
                    f"historical_data: {hit['fact_type']}={hit['fact_value']} (trust={hit['trust_level']})"
                    for hit in memory_hits[:5]
                ]
                # LangMem semantic layer: read-only, additive context over the same facts
                # TieredMemory already vetted. Tagged trust_level=model_inferred by
                # LangMemBridge itself, so nothing downstream can mistake this for a
                # verified fact; it never feeds the decision-critical path.
                semantic_hits = await self.s.langmem.semantic_recall(customer_id, env.masked_text)
                historical_memory += [
                    f"semantic_recall: {hit['fact_value']} (trust={hit['trust_level']})"
                    for hit in semantic_hits
                ]
                return {
                    "masked_text": env.masked_text,
                    "quarantined": True,
                    "injection_flag": env.injection_flag or state.get("injection_flag", False),
                    "pii_entities": state.get("pii_entities", []),
                    "conversation_context": [x["masked_text"] for x in reversed(previous)] + historical_memory,
                    "memory_hits": memory_hits[:5],
                }
            except Exception as exc:
                self.s._transition(state["case_id"], CaseState.FAILED.value, "NONE")
                return {"errors": ["INGRESS_FAILURE", str(exc)], "case_state": CaseState.FAILED.value, "route": "finalize"}

    async def supervisor(self, state: CopilotState):
        if state.get("injection_flag"):
            return {
                "route": "finalize",
                "case_state": CaseState.NEEDS_INFO.value,
                "errors": list({*(state.get("errors") or []), "SECURITY_FLAG"}),
            }

        if state.get("case_state") in {CaseState.NEEDS_INFO.value, CaseState.FAILED.value}:
            return {"route": "finalize"}

        if not state.get("classification"):
            return {"route": "classification_agent"}

        intent = state["classification"].intent
        if intent in ("ambiguous", "out_of_scope"):
            return {"route": "finalize", "case_state": CaseState.NEEDS_INFO.value}

        if intent == "policy_query":
            if not state.get("policy_hits"):
                return {"route": "chargeback_rules_agent"}
            return {"route": "finalize", "case_state": CaseState.RESOLVED.value}

        if not state.get("transaction"):
            return {"route": "case_data_agent"}
        if not state.get("fraud"):
            return {"route": "fraud_scoring_agent"}
        if not state.get("policy"):
            return {"route": "chargeback_rules_agent"}
        if not state.get("recommendation"):
            return {"route": "decision_engine"}

        if state["recommendation"].human_review_required and not state.get("disposition"):
            if not state.get("review_resume"):
                return {"route": "human_review"}
            return {"route": "disposition_commit"}

        return {"route": "finalize", "case_state": CaseState.RESOLVED.value}

    @staticmethod
    def route(state: CopilotState):
        return state.get("route", "END")

    def _transition(self, case_id: str, target: str, review_state: str | None = None) -> None:
        self.s.db.set_state(case_id, target, review_state=review_state)

    async def classification_agent(self, state: CopilotState):
        with self.s.telemetry.span("agent.classification", case_id=state["case_id"], run_id=state["run_id"]):
            try:
                result = await asyncio.to_thread(
                    self.s.classifier.run,
                    state["masked_text"],
                    state.get("conversation_context", []),
                    state.get("claim_hints", {}),
                    state["run_id"],
                )
            except Exception as exc:
                code = getattr(exc, "args", ["PROV_ALL_FAILED"])[0]
                return {
                    "errors": [str(code)],
                    "case_state": CaseState.NEEDS_INFO.value,
                    "route": "finalize",
                }
            self._transition(state["case_id"], CaseState.CLASSIFIED.value, "NONE")
            return {
                "classification": result,
                "transaction_id": result.transaction_id or state.get("transaction_id"),
                "case_state": CaseState.CLASSIFIED.value,
            }

    async def case_data_agent(self, state: CopilotState):
        transaction_id = state.get("transaction_id")
        if not transaction_id:
            try:
                txns = await self.s.mcp_recent(state)
            except Exception as exc:
                code = "TOOL_TIMEOUT" if isinstance(exc, MCPToolTimeout) else "TOOL_DATA_UNAVAILABLE"
                self._transition(state["case_id"], CaseState.NEEDS_INFO.value, "NONE")
                return {"errors": [code, f"{type(exc).__name__}: {exc}"], "case_state": CaseState.NEEDS_INFO.value, "route": "finalize"}
            candidates = self._resolve_transaction_candidates(state, txns)
            if len(candidates) != 1:
                self._transition(state["case_id"], CaseState.NEEDS_INFO.value, "NONE")
                return {
                    "transaction_candidates": [self._public_tx(x) for x in candidates[:5]],
                    "case_state": CaseState.NEEDS_INFO.value,
                    "route": "finalize",
                }
            transaction_id = candidates[0]["transaction_id"]
            selected_source = "deterministic_candidate_match"
        else:
            selected_source = "case_or_user_selection"

        try:
            tx, history, profile, prior, account_snapshot = await self.s.mcp_data({**state, "transaction_id": transaction_id})
        except Exception as exc:
            code = "TOOL_TIMEOUT" if isinstance(exc, MCPToolTimeout) else "TOOL_DATA_UNAVAILABLE"
            self._transition(state["case_id"], CaseState.NEEDS_INFO.value, "NONE")
            return {
                "errors": [code, f"{type(exc).__name__}: {exc}"],
                "case_state": CaseState.NEEDS_INFO.value,
                "route": "finalize",
                "transaction_candidates": [],
            }
        self._transition(state["case_id"], CaseState.DATA_COLLECTED.value, "NONE")
        return {
            "transaction": tx,
            "history": history,
            "profile": profile,
            "prior": prior,
            "account_snapshot": account_snapshot,
            "transaction_id": transaction_id,
            "selected_transaction_source": selected_source,
            "case_state": CaseState.DATA_COLLECTED.value,
        }

    @staticmethod
    def _public_tx(tx: dict[str, Any]) -> dict[str, Any]:
        return {
            "transaction_id": tx["transaction_id"],
            "amount": tx["amount"],
            "currency": tx.get("currency", "INR"),
            "merchant": tx["merchant"],
            "category": tx["category"],
            "timestamp": tx["timestamp"],
            "channel": tx.get("channel"),
        }

    @staticmethod
    def _resolve_transaction_candidates(state: CopilotState, txns: list[dict[str, Any]]):
        classification = state["classification"]
        text = state["masked_text"].lower()
        amount = classification.claimed_amount
        merchant = classification.claimed_merchant.lower() if classification.claimed_merchant else None
        scored: list[tuple[int, dict[str, Any]]] = []
        for tx in txns:
            score = 0
            if amount is not None and str(tx["amount"]) == str(amount):
                score += 3
            elif str(tx["amount"]) in text:
                score += 2
            if merchant and merchant in tx["merchant"].lower():
                score += 3
            elif tx["merchant"].lower() in text:
                score += 2
            if tx["transaction_id"].lower() in text:
                score += 5
            if score:
                scored.append((score, tx))
        scored.sort(key=lambda x: (-x[0], x[1]["timestamp"]))
        if scored and scored[0][0] > (scored[1][0] if len(scored) > 1 else -1):
            return [scored[0][1]]
        return [x[1] for x in scored]

    async def fraud_scoring_agent(self, state: CopilotState):
        with self.s.telemetry.span("agent.fraud_scoring", case_id=state["case_id"], run_id=state["run_id"]):
            f = await asyncio.to_thread(
                self.s.fraud_agent.run,
                self.s.txn_model(state),
                self.s.history_models(state),
                self.s.profile_model(state),
                self.s.prior_models(state),
            )
            self._transition(state["case_id"], CaseState.FRAUD_ASSESSED.value, "NONE")
            return {"fraud": f, "case_state": CaseState.FRAUD_ASSESSED.value}

    async def chargeback_rules_agent(self, state: CopilotState):
        query = ((state["classification"].intent if state.get("classification") else "dispute") + " " + state["masked_text"])
        with self.s.telemetry.span("agent.chargeback_rules", case_id=state["case_id"], run_id=state["run_id"]):
            try:
                # Consume the required MCP resource through the adapter. Retrieved resource text is treated as data only.
                resource_text = await self.s.mcp.resource("policy://chargeback/manual")
                hits = await asyncio.to_thread(self.s.rules_agent.retrieve, query, state["run_id"])
            except Exception:
                self._transition(state["case_id"], CaseState.NEEDS_INFO.value, "NONE")
                return {
                    "policy_hits": [],
                    "policy_resource_seen": False,
                    "errors": ["RAG_UNAVAILABLE"],
                    "case_state": CaseState.NEEDS_INFO.value,
                    "route": "finalize",
                }
            out: dict[str, Any] = {
                "policy_hits": hits,
                "policy_resource_seen": bool(resource_text),
            }
            if state.get("transaction") and state.get("fraud"):
                po = self.s.policy.evaluate(
                    state["classification"], self.s.txn_model(state), state["fraud"], True, hits
                )
                if not po.matched:
                    self._transition(state["case_id"], CaseState.NEEDS_INFO.value, "NONE")
                    out.update({
                        "policy": po,
                        "case_state": CaseState.NEEDS_INFO.value,
                        "errors": ["POLICY_MATCH_NOT_FOUND"],
                        "route": "finalize",
                    })
                else:
                    self._transition(state["case_id"], CaseState.POLICY_EVALUATED.value, "NONE")
                    out.update({"policy": po, "case_state": CaseState.POLICY_EVALUATED.value})
            return out

    async def decision_engine(self, state: CopilotState):
        with self.s.telemetry.span("agent.decision_engine", case_id=state["case_id"], run_id=state["run_id"]):
            txn = self.s.txn_model(state)
            try:
                rec = self.s.decision.decide(
                    state["case_id"], state["classification"], state["fraud"], state["policy"], txn=txn
                )
                self.s.invariants.validate(rec)
                validate_recommendation(rec.model_dump())
            except Exception as exc:
                self._transition(state["case_id"], CaseState.FAILED.value, "NONE")
                self.s.db.audit(state["case_id"], "decision_failure", {"run_id": state["run_id"], "error": str(exc), "actor_id": state["actor_id"]})
                return {"errors": ["INVARIANT_VIOLATION"], "case_state": CaseState.FAILED.value, "route": "finalize"}

            sid = self.s.db.snapshot(
                state["case_id"],
                {
                    "masked_text": state["masked_text"],
                    "classification": state["classification"].model_dump(),
                    "transaction": txn.model_dump(mode="json"),
                    "history": state.get("history", []),
                    "profile": state.get("profile"),
                    "prior": state.get("prior", []),
                    "account_snapshot": state.get("account_snapshot"),
                    "fraud": state["fraud"].model_dump(),
                    "policy": state["policy"].model_dump(),
                    "rag": state.get("policy_hits", []),
                    "memory_hits": state.get("memory_hits", []),
                },
            )
            self._transition(state["case_id"], CaseState.RECOMMENDATION_READY.value, "NONE")
            self.s.db.audit(
                state["case_id"],
                "recommendation",
                {
                    "run_id": state["run_id"],
                    "actor_id": state["actor_id"],
                    "recommendation": rec.model_dump(mode="json"),
                    "snapshot_id": sid,
                },
            )
            out = {"recommendation": rec, "snapshot_id": sid}
            if rec.human_review_required:
                task = self.s.db.create_review(
                    state["case_id"], rec.recommendation_id, recommendation_creator=state["actor_id"]
                )
                self._transition(state["case_id"], CaseState.PENDING_REVIEW.value, "PENDING")
                self.s.db.enqueue(
                    "review.created",
                    f"review:{task.task_id}",
                    {"task_id": task.task_id, "case_id": state["case_id"], "recommendation_id": rec.recommendation_id},
                )
                out.update({"review_task_id": task.task_id, "case_state": CaseState.PENDING_REVIEW.value})
            else:
                self._transition(state["case_id"], CaseState.RESOLVED.value, "NONE")
                out["case_state"] = CaseState.RESOLVED.value
            return out

    async def human_review(self, state: CopilotState):
        # No side effects before interrupt: resume may re-execute this node.
        if self.journey:
            self.journey.start("human_review", input_summary("human_review", state))
        try:
            payload = interrupt({"task_id": state["review_task_id"], "case_id": state["case_id"]})
        except Exception as exc:
            # LangGraph represents interrupt/pause control flow with a graph interrupt exception.
            if exc.__class__.__name__ == "GraphInterrupt":
                if self.journey:
                    self.journey.pause("human_review", "Workflow paused awaiting authorized reviewer")
                raise
            if self.journey:
                self.journey.fail("human_review", exc)
            raise
        if self.journey:
            self.journey.complete("human_review", "Reviewer response received")
        return {"review_resume": payload}

    async def disposition_commit(self, state: CopilotState):
        task_id = state["review_task_id"]
        row = self.s.db.get_review(task_id)
        if not row or row["status"] != "RESOLVED":
            return {"case_state": CaseState.PENDING_REVIEW.value}
        disp = FinalDisposition.model_validate_json(row["resolution_json"])
        return {"disposition": disp.model_dump(mode="json"), "case_state": CaseState.RESOLVED.value}

    async def finalize(self, state: CopilotState):
        case_state = state.get("case_state", CaseState.RESOLVED.value)
        if state.get("injection_flag"):
            customer_view = "We cannot process the submitted instructions because the message contains content that is not accepted as a dispute instruction."
        elif case_state == CaseState.NEEDS_INFO.value:
            customer_view = self.s.render_customer(state)
        else:
            customer_view = self.s.render_customer(state)

        customer_view = sanitize_output(customer_view)
        validate_customer_view(customer_view)

        current = self.s.db.get_case(state["case_id"])
        review_state = current["review_state"]
        if state.get("disposition"):
            review_state = "RESOLVED"
        elif case_state == CaseState.PENDING_REVIEW.value:
            review_state = "PENDING"
        elif case_state != CaseState.NEEDS_INFO.value:
            review_state = "NONE"

        release_payload = {
            "case_state": case_state,
            "review_task": {"task_id": state.get("review_task_id")} if state.get("review_task_id") else None,
            "recommendation": state.get("recommendation").model_dump(mode="json") if state.get("recommendation") else None,
            "injection_flag": state.get("injection_flag", False),
        }
        try:
            validate_release_invariants(release_payload)
        except Exception as exc:
            self.s.db.set_state(state["case_id"], CaseState.FAILED.value, review_state, "FAILED")
            self.s.db.audit(
                state["case_id"],
                "release_gate_failure",
                {
                    "run_id": state["run_id"],
                    "actor_id": state["actor_id"],
                    "error": str(exc),
                },
            )
            return {
                "case_state": CaseState.FAILED.value,
                "audit_state": "FAILED",
                "review_state": review_state,
                "customer_view": "The case could not be released automatically. It has been routed for investigation.",
            }

        # Validate first, then commit the audit event, then mark the case releasable.
        # A failed release gate can therefore never be observed as RELEASABLE.
        disposition_row = self.s.db.get_review(state["review_task_id"]) if state.get("review_task_id") else None
        audit_actor = (
            disposition_row["reviewer_id"]
            if disposition_row and disposition_row["reviewer_id"]
            else state["actor_id"]
        )
        event_hash = self.s.db.audit(
            state["case_id"],
            "finalize",
            {
                "run_id": state["run_id"],
                "actor_id": audit_actor,
                "case_state": case_state,
                "snapshot_id": state.get("snapshot_id"),
                "release_candidate": True,
            },
        )
        self.s.db.set_state(state["case_id"], case_state, review_state, "RELEASABLE")

        if state.get("recommendation") and case_state == CaseState.RESOLVED.value:
            rec = state["recommendation"]
            customer_id = current["customer_id"]
            self.s.memory.write(
                customer_id,
                "last_dispute_action",
                rec.primary_action.value,
                self.s.memory.system_verified_level,
                "deterministic_decision",
            )
            self.s.memory.write(
                customer_id,
                "last_dispute_type",
                state["classification"].intent,
                self.s.memory.system_verified_level,
                "semantic_classification_plus_policy",
            )
            await self.s.langmem.remember(customer_id, "last_dispute_action", rec.primary_action.value)
            await self.s.langmem.remember(customer_id, "last_dispute_type", state["classification"].intent)

        return {
            "case_state": case_state,
            "audit_state": "RELEASABLE",
            "review_state": review_state,
            "customer_view": customer_view,
            "audit_event_hash": event_hash,
        }

    async def ainvoke(self, initial: CopilotState, thread_id: str):
        return await self.graph.ainvoke(
            initial,
            {"configurable": {"thread_id": thread_id}, "recursion_limit": self.s.settings.max_graph_steps},
        )
