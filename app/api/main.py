from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.core.auth import AuthenticationError, AuthorizationError, authenticate, require_roles
from app.core.data_plane import DataPlane
from app.core.outbox import OutboxWorker
from app.models import DisputeClaim, Principal, Role
from app.workflow import Copilot

logger = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parents[2]
copilot = Copilot()
data = DataPlane()

app = FastAPI(title="Transaction Dispute Copilot", version="0.8.0")

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app)
except Exception:
    pass


class OpenCase(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    transaction_id: str | None = Field(default=None, max_length=100)


class RunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    selected_transaction_id: str | None = Field(default=None, max_length=100)
    claimed_amount: str | None = Field(default=None, max_length=32)
    claimed_date: str | None = Field(default=None, max_length=32)
    claimed_merchant: str | None = Field(default=None, max_length=200)


class ClaimRequest(BaseModel):
    expected_version: int = Field(ge=0)


class ResolveRequest(BaseModel):
    expected_version: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=40)
    reason_code: str = Field(default="EVIDENCE_SUFFICIENT", min_length=1, max_length=80)
    note: str = Field(default="", max_length=300)


def current_principal(authorization: str | None = Header(default=None, alias="Authorization")) -> Principal:
    try:
        return authenticate(authorization)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    except RuntimeError as exc:
        logger.error("Authentication configuration failure: %s", exc)
        raise HTTPException(status_code=503, detail="AUTHENTICATION_UNAVAILABLE") from exc


def role_guard(*roles: Role):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        try:
            return require_roles(principal, *roles)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return dependency


def authorize_case_or_403(principal: Principal, case_id: str):
    try:
        return copilot.s.authorize(principal, case_id)
    except PermissionError as exc:
        if str(exc) == "CASE_NOT_FOUND":
            raise HTTPException(status_code=404, detail="CASE_NOT_FOUND") from exc
        raise HTTPException(status_code=403, detail="CASE_ACCESS_DENIED") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/")
def root():
    html = (BASE / "frontend" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/details")
def health_details(principal: Principal = Depends(role_guard(Role.analyst, Role.reviewer))):
    _ = principal
    return {
        "status": "ok",
        "semantic_mode": copilot.settings.semantic_mode,
        "gemini_model": copilot.settings.gemini_model,
        "groq_model": copilot.settings.groq_model,
        "rag_mode": copilot.settings.rag_mode,
        "pii_mode": copilot.settings.pii_mode,
        "langmem_available": copilot.s.langmem.available_status(),
        "phoenix_available": copilot.s.telemetry.phoenix_available,
    }


@app.get("/api/customers")
def customers(principal: Principal = Depends(current_principal)):
    if principal.role == Role.customer:
        if not principal.customer_scope:
            raise HTTPException(403, "CUSTOMER_SCOPE_REQUIRED")
        try:
            dashboard = data.dashboard(principal.customer_scope)
        except KeyError as exc:
            raise HTTPException(404, "CUSTOMER_NOT_FOUND") from exc
        return [dashboard["profile"]]
    if principal.role == Role.analyst:
        allowed = set(data.portfolios.get(principal.actor_id, []))
        return [p for p in data.list_customers() if p["customer_id"] in allowed]
    return data.list_customers()


@app.get("/api/analyst/queue")
def analyst_queue(principal: Principal = Depends(role_guard(Role.analyst, Role.reviewer))):
    rows = copilot.db.cases_for_queue()
    if principal.role == Role.analyst:
        allowed = set(data.portfolios.get(principal.actor_id, []))
        return [r for r in rows if r["customer_id"] in allowed]
    return rows


@app.get("/api/customers/{customer_id}/dashboard")
def customer_dashboard(customer_id: str, principal: Principal = Depends(current_principal)):
    if principal.role == Role.customer and principal.customer_scope != customer_id:
        raise HTTPException(403, "CUSTOMER_ACCESS_DENIED")
    if principal.role == Role.analyst and customer_id not in data.portfolios.get(principal.actor_id, []):
        raise HTTPException(403, "CUSTOMER_ACCESS_DENIED")
    try:
        return data.dashboard(customer_id)
    except KeyError as exc:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND") from exc


@app.get("/api/customers/{customer_id}/transactions")
def customer_transactions(customer_id: str, principal: Principal = Depends(current_principal)):
    dashboard = customer_dashboard(customer_id, principal)
    return dashboard["transactions"]


@app.get("/api/customers/{customer_id}/statements")
def customer_statements(customer_id: str, principal: Principal = Depends(current_principal)):
    dashboard = customer_dashboard(customer_id, principal)
    return dashboard["statements"]


@app.get("/api/customers/{customer_id}/statements/{statement_id}")
def customer_statement(customer_id: str, statement_id: str, principal: Principal = Depends(current_principal)):
    customer_dashboard(customer_id, principal)
    try:
        return data.statement_detail(customer_id, statement_id)
    except KeyError as exc:
        raise HTTPException(404, "STATEMENT_NOT_FOUND") from exc


@app.get("/api/customers/{customer_id}/account")
def customer_account(customer_id: str, principal: Principal = Depends(current_principal)):
    dashboard = customer_dashboard(customer_id, principal)
    return dashboard["account"]


@app.get("/api/customers/{customer_id}/card")
def customer_card(customer_id: str, principal: Principal = Depends(current_principal)):
    dashboard = customer_dashboard(customer_id, principal)
    return dashboard["card"]


@app.post("/api/cases")
def create(req: OpenCase, principal: Principal = Depends(role_guard(Role.analyst))):
    try:
        copilot.s.authorize_case_creation(principal, req.customer_id)
        return {"case_id": copilot.db.create_case(req.customer_id, principal.actor_id, req.transaction_id)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, "CASE_CREATE_INVALID") from exc


@app.get("/api/cases")
def list_cases(customer_id: str | None = None, principal: Principal = Depends(current_principal)):
    if customer_id:
        if principal.role == Role.customer and principal.customer_scope != customer_id:
            raise HTTPException(403, "CASE_ACCESS_DENIED")
        if principal.role == Role.analyst and customer_id not in data.portfolios.get(principal.actor_id, []):
            raise HTTPException(403, "CASE_ACCESS_DENIED")
        return copilot.db.cases_for_customer(customer_id)
    rows = copilot.db.cases_for_queue()
    if principal.role == Role.analyst:
        allowed = set(data.portfolios.get(principal.actor_id, []))
        return [r for r in rows if r["customer_id"] in allowed]
    if principal.role == Role.customer:
        return [r for r in rows if r["customer_id"] == principal.customer_scope]
    return rows


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, principal: Principal = Depends(current_principal)):
    authorize_case_or_403(principal, case_id)
    row = copilot.db.get_case(case_id)
    if not row:
        raise HTTPException(404, "CASE_NOT_FOUND")
    result = dict(row)
    result["audit"] = copilot.db.get_audit(case_id)
    review_task_id = result.get("review_task_id")
    if review_task_id:
        review_row = copilot.db.get_review(review_task_id)
        if review_row:
            result["review_task"] = dict(review_row)
    return result


@app.post("/api/cases/{case_id}/run")
async def run(
    case_id: str,
    req: RunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(role_guard(Role.analyst)),
):
    authorize_case_or_403(principal, case_id)
    claim = DisputeClaim(
        dispute_message=req.text,
        claimed_transaction_id=req.selected_transaction_id,
        claimed_amount=req.claimed_amount,
        claimed_date=req.claimed_date,
        claimed_merchant=req.claimed_merchant,
    )
    try:
        response = await asyncio.wait_for(
            copilot.run_async(
                case_id,
                principal.actor_id,
                req.text,
                principal.role,
                idempotency_key=idempotency_key,
                claim=claim,
            ),
            timeout=copilot.settings.max_run_seconds,
        )
        return response.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except asyncio.TimeoutError as exc:
        logger.error("Workflow timeout case=%s", case_id)
        raise HTTPException(504, "WORKFLOW_TIMEOUT") from exc
    except ValueError as exc:
        if str(exc) in {"OP_IN_PROGRESS", "STATE_VERSION_CONFLICT"}:
            raise HTTPException(409, str(exc)) from exc
        raise HTTPException(400, "WORKFLOW_INPUT_INVALID") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow failure case=%s error=%s", case_id, type(exc).__name__)
        raise HTTPException(500, "WORKFLOW_FAILED") from exc


@app.get("/api/cases/{case_id}/audit")
def audit(case_id: str, principal: Principal = Depends(current_principal)):
    authorize_case_or_403(principal, case_id)
    return copilot.db.get_audit(case_id)


@app.get("/api/reviews/{task_id}")
def get_review(task_id: str, principal: Principal = Depends(role_guard(Role.reviewer))):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    authorize_case_or_403(principal, row["case_id"])
    return dict(row)


@app.post("/api/reviews/{task_id}/claim")
def claim(task_id: str, req: ClaimRequest, principal: Principal = Depends(role_guard(Role.reviewer))):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    authorize_case_or_403(principal, row["case_id"])
    if not copilot.db.claim_review(task_id, principal.actor_id, req.expected_version):
        raise HTTPException(409, "REVIEW_CONFLICT")
    return {"status": "claimed", "task_id": task_id, "reviewer_id": principal.actor_id, "version": req.expected_version + 1}


@app.post("/api/reviews/{task_id}/resolve")
async def resolve(task_id: str, req: ResolveRequest, principal: Principal = Depends(role_guard(Role.reviewer))):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    authorize_case_or_403(principal, row["case_id"])
    if not copilot.db.resolve_review(task_id, principal.actor_id, req.expected_version, row["case_id"], req.action, req.reason_code, req.note):
        raise HTTPException(409, "REVIEW_CONFLICT")
    try:
        result = await asyncio.wait_for(copilot.resume_review(task_id, principal.actor_id), timeout=copilot.settings.max_run_seconds)
    except asyncio.TimeoutError as exc:
        logger.error("Review resume timeout task=%s", task_id)
        raise HTTPException(504, "REVIEW_RESUME_TIMEOUT") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Review resume failed task=%s error=%s", task_id, type(exc).__name__)
        raise HTTPException(503, "REVIEW_RESUME_FAILED_RETRY_ALLOWED") from exc
    return {
        "status": "resolved",
        "case_id": row["case_id"],
        "task_id": task_id,
        "action": req.action,
        "workflow": result,
    }


@app.post("/api/outbox/drain")
def drain(principal: Principal = Depends(role_guard(Role.reviewer))):
    _ = principal
    return {"delivered": OutboxWorker(copilot.db, copilot.settings.outbox_sink, max_attempts=copilot.settings.outbox_max_attempts).deliver_once()}


@app.get("/api/rag/search")
def rag(q: str = "", principal: Principal = Depends(role_guard(Role.analyst, Role.reviewer))):
    _ = principal
    q = q.strip()
    if not q or len(q) > 1000:
        raise HTTPException(400, "QUERY_INVALID")
    try:
        return copilot.s.rag.search(q)
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG failure: %s", type(exc).__name__)
        raise HTTPException(503, "RAG_UNAVAILABLE") from exc
