from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from decimal import Decimal

logger = logging.getLogger("dispute_copilot")

from app.core.db import DB
from app.core.data_plane import DataPlane
from app.core.security import mint_context
from app.core.semantic import SemanticGateway
from app.core.engines import FraudEngine, PolicyEngine, DecisionEngine, InvariantEngine
from app.core.pii import PIIService
from app.core.settings import Settings
from src.context.engineering import ContextEngineer
from src.observability.tracing import TraceManager
from src.agents.workers import DisputeClassificationAgent, FraudScoringAgent, ChargebackRulesAgent
from src.graph import CopilotGraph
from src.memory.store import TieredMemory, LangMemBridge
from src.mcp_client import BankingMCPClient
from src.tools.rag_tool import AgenticPolicyRAG
from src.execution.journey import ExecutionJourney
from src.guardrails.validators import validate_customer_view
from app.models import (
    AccountSnapshot,
    CaseState,
    DecisionProvenance,
    FinalDisposition,
    Principal,
    Role,
    RunResponse,
    DisputeClaim,
)


class Services:
    def __init__(self, db: DB, settings: Settings):
        self.db = db
        self.settings = settings
        self.data = DataPlane()
        self.semantic = SemanticGateway(
            settings.semantic_mode,
            max_provider_calls=settings.max_provider_calls,
            classification_budget=settings.classification_budget,
            rag_rewrite_budget=settings.rag_rewrite_budget,
            provider_max_attempts=settings.provider_max_attempts,
            max_workers=settings.semantic_max_workers,
            max_context_tokens=settings.semantic_max_context_tokens,
        )
        self.fraud = FraudEngine()
        self.policy = PolicyEngine()
        self.decision = DecisionEngine()
        self.invariants = InvariantEngine()
        self.pii = PIIService(settings.pii_mode)
        self.context = ContextEngineer()
        self.rag = AgenticPolicyRAG(
            settings.rag_path,
            settings.embedding_model,
            settings.rag_mode,
            semantic_gateway=self.semantic,
        )
        self.telemetry = TraceManager(settings.otel_enabled, settings.phoenix_endpoint)
        self.memory = TieredMemory(settings.memory_path)
        self.langmem = LangMemBridge(self.memory)
        self.classifier = DisputeClassificationAgent(self.semantic)
        self.fraud_agent = FraudScoringAgent(self.fraud)
        self.rules_agent = ChargebackRulesAgent(self.rag, self.policy)
        self.secret = settings.access_secret
        self.mcp = BankingMCPClient()

    def principal(self, actor: str, role: Role | str) -> Principal:
        role_enum = Role(role)
        scope = actor.split(":", 1)[1] if role_enum == Role.customer and ":" in actor else None
        return Principal(actor_id=actor, role=role_enum, customer_scope=scope, team="fraud-ops")

    def authorize(self, principal: Principal, case_id: str):
        row = self.db.get_case(case_id)
        if not row:
            raise PermissionError("CASE_NOT_FOUND")
        if principal.role == Role.analyst:
            portfolio = self.data.portfolios.get(principal.actor_id, [])
            if row["customer_id"] not in portfolio:
                raise PermissionError("CASE_DENIED")
        elif principal.role == Role.reviewer:
            # Reviewer identity is already derived from the authenticated principal.
            # Do not encode authorization semantics in a mutable client-provided ID format.
            return row
        elif principal.role == Role.customer:
            if principal.customer_scope != row["customer_id"]:
                raise PermissionError("CASE_DENIED")
        return row

    def authorize_case_creation(self, principal: Principal, customer_id: str):
        if principal.role != Role.analyst:
            raise PermissionError("CASE_CREATE_DENIED")
        portfolio = self.data.portfolios.get(principal.actor_id, [])
        if customer_id not in portfolio:
            raise PermissionError("CASE_CREATE_DENIED")

    def ctx(self, principal: Principal, row, permissions):
        return mint_context(
            principal,
            row["case_id"],
            row["customer_id"],
            permissions,
            self.secret,
        )

    async def mcp_data(self, state):
        principal = self.principal(state["actor_id"], state["role"])
        row = self.db.get_case(state["case_id"])
        tid = state["transaction_id"]

        tx_token = self.ctx(principal, row, ["txn:read"])
        hist_token = self.ctx(principal, row, ["history:read"])
        prof_token = self.ctx(principal, row, ["profile:read"])
        acct_token = self.ctx(principal, row, ["account:read"])

        tx = await self.mcp.call("get_transaction", {"access_context": tx_token, "transaction_id": tid})
        tx_ts = tx.get("timestamp") if isinstance(tx, dict) else None
        hist, profile, prior, account, statements = await asyncio.gather(
            self.mcp.call("get_recent_transactions", {"access_context": hist_token, "window_days": 90, "limit": 20, "as_of": tx_ts}, expect="list"),
            self.mcp.call("get_customer_profile", {"access_context": prof_token}),
            self.mcp.call("get_prior_disputes", {"access_context": hist_token, "limit": 10}, expect="list"),
            self.mcp.call("get_account_summary", {"access_context": acct_token}),
            self.mcp.call("get_statements", {"access_context": acct_token, "limit": 12}, expect="list"),
        )

        account_snapshot = AccountSnapshot(
            account=account["account"],
            card=account["card"],
            statements=statements,
        )
        return tx, hist, profile, prior, account_snapshot.model_dump(mode="json")

    async def mcp_recent(self, state):
        principal = self.principal(state["actor_id"], state["role"])
        row = self.db.get_case(state["case_id"])
        token = self.ctx(principal, row, ["history:read"])
        return await self.mcp.call("get_recent_transactions", {"access_context": token, "window_days": 90, "limit": 20}, expect="list")

    @staticmethod
    def txn_model(state):
        from app.models import Transaction
        return Transaction.model_validate(state["transaction"])

    @staticmethod
    def history_models(state):
        from app.models import Transaction
        return [Transaction.model_validate(x) for x in state.get("history", [])]

    @staticmethod
    def profile_model(state):
        from app.models import CustomerProfile
        return CustomerProfile.model_validate(state["profile"])

    @staticmethod
    def prior_models(state):
        from app.models import PriorDispute
        return [PriorDispute.model_validate(x) for x in state.get("prior", [])]

    def render_customer(self, state):
        case_state = state.get("case_state")
        if state.get("injection_flag"):
            return "We cannot process the submitted instructions because the message contains content that is not accepted as a dispute instruction."
        if case_state == CaseState.NEEDS_INFO.value:
            if state.get("transaction_candidates"):
                return "Please select the transaction you want to dispute before the investigation continues."
            return "Please provide the transaction reference or select the transaction from your account activity."
        if state.get("disposition"):
            return "Your dispute has completed human review. The final disposition is recorded on the case."
        rec = state.get("recommendation")
        if not rec:
            return "Your dispute is under review."
        if rec.human_review_required:
            return "Your dispute is under specialist review. No final customer outcome is released until review is complete."
        return f"Your dispute is currently processed with next action: {rec.primary_action.value.replace('_', ' ')}."

    def _transition(self, case_id: str, target: str, review_state: str | None = None):
        self.db.set_state(case_id, target, review_state=review_state)


class Copilot:
    def __init__(self, db=None, settings=None):
        self.settings = settings or Settings()
        self.db = db or DB(self.settings.db_path)
        self.s = Services(self.db, self.settings)

    def run(self, case_id, actor, text, role=Role.analyst, idempotency_key=None, claim: DisputeClaim | None = None):
        return asyncio.run(self.run_async(case_id, actor, text, role, idempotency_key=idempotency_key, claim=claim))

    async def run_async(
        self,
        case_id,
        actor,
        text,
        role=Role.analyst,
        idempotency_key=None,
        claim: DisputeClaim | None = None,
    ):
        principal = self.s.principal(actor, role)
        row = self.s.authorize(principal, case_id)
        if not text or len(text) > 5000:
            raise ValueError("DISPUTE_TEXT_INVALID")
        claim = claim or DisputeClaim(dispute_message=text)

        idem = idempotency_key or ("run:" + hashlib.sha256(f"{case_id}|{actor}|{text}".encode()).hexdigest())
        input_hash = hashlib.sha256(text.encode()).hexdigest()
        existing = self.db.begin_operation(idem, "copilot_run", case_id, input_hash)
        if existing["status"] == "COMPLETED":
            return RunResponse.model_validate_json(existing["result_json"])

        run_id = "RUN-" + hashlib.sha256(f"{case_id}:{time.time_ns()}".encode()).hexdigest()[:12]
        journey = ExecutionJourney(run_id)

        # Security boundary: raw text is scanned/masked BEFORE it enters graph state/checkpoints/traces.
        try:
            pii = await asyncio.to_thread(self.s.pii.scan, claim.dispute_message)
        except Exception as exc:
            error_code = self._safe_error_code(exc)
            try:
                self.db.set_state(case_id, CaseState.FAILED.value, review_state="NONE", audit_state="FAILED")
            except Exception:
                logger.exception("Failed to persist PII-gate case state case=%s", case_id)
            try:
                self.db.fail_operation(existing, error_code)
            except Exception:
                logger.exception("Failed to persist PII-gate operation failure case=%s", case_id)
            self.db.audit(case_id, "pii_gate_failure", {"run_id": run_id, "actor_id": actor, "error_code": error_code})
            raise
        claim_hints = {
            "transaction_id": claim.claimed_transaction_id,
            "claimed_amount": str(claim.claimed_amount) if claim.claimed_amount is not None else None,
            "claimed_merchant": claim.claimed_merchant,
            "claimed_date": claim.claimed_date,
        }
        initial = {
            "case_id": case_id,
            "actor_id": actor,
            "role": role.value if isinstance(role, Role) else str(role),
            "masked_text": pii.masked_text,
            "claim_hints": claim_hints,
            "injection_flag": self.s.context.INJECTION.search(pii.masked_text) is not None,
            "pii_entities": pii.entities,
            "transaction_id": row["primary_transaction_id"] or claim.claimed_transaction_id,
            "case_state": CaseState.NEW.value,
            "audit_state": "PENDING",
            "run_id": run_id,
        }
        journey.set_inputs(
            {
                "customer_id": row["customer_id"],
                "transaction_id": initial.get("transaction_id"),
                "claimed_amount": claim_hints.get("claimed_amount"),
                "claimed_merchant": claim_hints.get("claimed_merchant"),
                "claimed_date": claim_hints.get("claimed_date"),
                "customer_statement_received": bool(claim.dispute_message.strip()),
            }
        )

        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            async with AsyncSqliteSaver.from_conn_string(self.settings.checkpoint_path) as cp:
                graph = CopilotGraph(self.s, cp, journey=journey)
                with self.s.telemetry.span("copilot.run", case_id=case_id, run_id=run_id) as span:
                    result = await asyncio.wait_for(
                        graph.ainvoke(initial, case_id),
                        timeout=self.settings.max_run_seconds,
                    )
                    if span is not None:
                        telemetry = self.s.semantic.telemetry_for(run_id)
                        for key, value in (telemetry.get("usage") or {}).items():
                            span.set_attribute(
                                f"llm.{key}",
                                int(value) if isinstance(value, int) else str(value),
                            )
                        span.set_attribute("llm.provider", telemetry.get("provider", "unknown"))
        except ImportError as exc:
            error_code = self._safe_error_code(exc)
            self.db.audit(
                case_id,
                "checkpointer_unavailable",
                {"run_id": run_id, "actor_id": actor, "error_code": error_code},
            )
            try:
                self.db.set_state(case_id, CaseState.FAILED.value, review_state="NONE", audit_state="FAILED")
            except Exception:
                logger.exception("Failed to persist checkpointer failure state case=%s", case_id)
            try:
                self.db.fail_operation(existing, error_code)
            finally:
                self.s.semantic.release_run(run_id)
            raise RuntimeError(
                "REQUIRED_CHECKPOINTER_UNAVAILABLE: install langgraph-checkpoint-sqlite"
            ) from exc
        except Exception as exc:
            self.db.audit(case_id, "workflow_failure", {"run_id": run_id, "actor_id": actor, "error_code": self._safe_error_code(exc)})
            try:
                self.db.set_state(case_id, CaseState.FAILED.value, "NONE", "FAILED")
            except Exception:
                pass
            try:
                self.db.fail_operation(existing, self._safe_error_code(exc))
            finally:
                self.s.semantic.release_run(run_id)
            raise

        rec = result.get("recommendation")
        review = None
        if result.get("review_task_id"):
            task_row = self.db.get_review(result["review_task_id"])
            if task_row:
                review = self.db.review_model(task_row)
        final_disposition = None
        if result.get("disposition"):
            final_disposition = FinalDisposition.model_validate(result["disposition"])

        semantic_telemetry = self.s.semantic.telemetry_for(run_id)
        successful_provider = next(
            (a for a in reversed(semantic_telemetry.get("attempts", [])) if a.get("status") == "SUCCESS"),
            None,
        )
        provenance = DecisionProvenance(
            run_id=run_id,
            case_id=case_id,
            provider_attempts=semantic_telemetry.get("attempts", []),
            model_id=(successful_provider or {}).get("model", semantic_telemetry.get("model", self.settings.gemini_model)),
            provider_used=(successful_provider or {}).get("provider", semantic_telemetry.get("provider", "unknown")),
            policy_version=self.s.policy.cfg["version"],
            config_hash=hashlib.sha256((Path(__file__).resolve().parents[1] / "config" / "decision_policy.yaml").read_bytes()).hexdigest(),
            inputs_snapshot_id=result.get("snapshot_id") or "not-created",
            prompt_hash=hashlib.sha256(pii.masked_text.encode()).hexdigest(),
            rag_index_hash=getattr(self.s.rag, "manifest_hash", None),
            created_at=time.time(),
        )

        response = RunResponse(
            case_id=case_id,
            run_id=run_id,
            case_state=CaseState(result.get("case_state", CaseState.NEEDS_INFO.value)),
            recommendation=rec,
            final_disposition=final_disposition,
            review_task=review,
            clarification=(
                ["Select a transaction to continue."]
                if result.get("case_state") == CaseState.NEEDS_INFO.value and result.get("transaction_candidates")
                else []
            ),
            transaction_candidates=result.get("transaction_candidates", []),
            customer_view=result.get("customer_view", ""),
            analyst_view={
                "classification": result["classification"].model_dump(mode="json") if result.get("classification") else None,
                "fraud": result["fraud"].model_dump(mode="json") if result.get("fraud") else None,
                "policy": result["policy"].model_dump(mode="json") if result.get("policy") else None,
                "escalation_reasons": rec.escalation_reasons if rec else [],
                "transaction": result.get("transaction"),
                "history": result.get("history", []),
                "warnings": result.get("warnings", []),
                "profile": result.get("profile"),
                "prior_disputes": result.get("prior", []),
                "account_snapshot": result.get("account_snapshot"),
                "policy_hits": result.get("policy_hits", []),
                "policy_resource_seen": result.get("policy_resource_seen", False),
                "memory_hits": result.get("memory_hits", []),
                "selected_transaction_source": result.get("selected_transaction_source"),
                "pii_entities": result.get("pii_entities", []),
                "errors": result.get("errors", []),
                "final_disposition": result.get("disposition"),
            },
            provenance=provenance,
            execution_journey=journey.to_dict(),
        )
        try:
            validate_customer_view(response.customer_view)
            self.db.finish_operation(existing, response.model_dump(mode="json"))
            return response
        finally:
            self.s.semantic.release_run(run_id)

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        return f"{type(exc).__name__.upper()}_FAILED"

    async def resume_review(self, task_id: str, reviewer_id: str | None = None):
        row = self.db.get_review(task_id)
        if not row:
            raise LookupError("REVIEW_NOT_FOUND")
        if row["status"] != "RESOLVED" or row["claimed_by"] != reviewer_id:
            raise PermissionError("REVIEW_RESUME_NOT_AUTHORIZED")
        case = self.db.get_case(row["case_id"])
        from langgraph.types import Command
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(self.settings.checkpoint_path) as cp:
            journey = ExecutionJourney(f"RESUME-{task_id}")
            graph = CopilotGraph(self.s, cp, journey=journey)
            with self.s.telemetry.span("copilot.review_resume", case_id=case["case_id"], run_id=f"RESUME-{task_id}"):
                return await graph.graph.ainvoke(
                    Command(resume={"task_id": task_id}),
                    {"configurable": {"thread_id": case["case_id"]}, "recursion_limit": self.settings.max_graph_steps},
                )
