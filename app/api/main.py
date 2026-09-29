from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.data_plane import DataPlane
from app.core.outbox import OutboxWorker
from app.models import DisputeClaim, Role
from app.workflow import Copilot

BASE = Path(__file__).resolve().parents[2]
copilot = Copilot()
data = DataPlane()

app = FastAPI(
    title="Transaction Dispute Copilot",
    version="0.6.0",
)

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:
    pass


class OpenCase(BaseModel):
    customer_id: str
    actor_id: str = "analyst:A-001"
    transaction_id: str | None = None


class RunRequest(BaseModel):
    actor_id: str = "analyst:A-001"
    text: str = Field(min_length=1, max_length=5000)
    role: Role = Role.analyst
    selected_transaction_id: str | None = None
    claimed_amount: str | None = None
    claimed_date: str | None = None
    claimed_merchant: str | None = None


class ClaimRequest(BaseModel):
    reviewer_id: str = "reviewer:R-001"
    expected_version: int


class ResolveRequest(BaseModel):
    reviewer_id: str = "reviewer:R-001"
    expected_version: int
    action: str
    reason_code: str = "EVIDENCE_SUFFICIENT"
    note: str = Field(default="", max_length=300)


@app.get("/")
def root():
    return FileResponse(str(BASE / "frontend" / "index.html"))


@app.get("/health")
def health():
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
def customers():
    return data.list_customers()


@app.get("/api/analyst/queue")
def analyst_queue():
    return copilot.db.cases_for_queue()


@app.get("/api/customers/{customer_id}/dashboard")
def customer_dashboard(customer_id: str):
    try:
        return data.dashboard(customer_id)
    except KeyError:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")


@app.get("/api/customers/{customer_id}/transactions")
def customer_transactions(customer_id: str):
    try:
        return data.dashboard(customer_id)["transactions"]
    except KeyError:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")


@app.get("/api/customers/{customer_id}/statements")
def customer_statements(customer_id: str):
    try:
        return data.dashboard(customer_id)["statements"]
    except KeyError:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")


@app.get("/api/customers/{customer_id}/statements/{statement_id}")
def customer_statement(customer_id: str, statement_id: str):
    try:
        return data.statement_detail(customer_id, statement_id)
    except KeyError:
        raise HTTPException(404, "STATEMENT_NOT_FOUND")


@app.get("/api/customers/{customer_id}/account")
def customer_account(customer_id: str):
    try:
        return data.dashboard(customer_id)["account"]
    except KeyError:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")


@app.get("/api/customers/{customer_id}/card")
def customer_card(customer_id: str):
    try:
        return data.dashboard(customer_id)["card"]
    except KeyError:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")


@app.post("/api/cases")
def create(req: OpenCase):
    try:
        principal = copilot.s.principal(req.actor_id, Role.analyst)
        copilot.s.authorize_case_creation(principal, req.customer_id)
        return {
            "case_id": copilot.db.create_case(
                req.customer_id,
                req.actor_id,
                req.transaction_id,
            )
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/cases")
def list_cases(customer_id: str | None = None):
    if customer_id:
        return copilot.db.cases_for_customer(customer_id)
    return copilot.db.cases_for_queue()


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
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
):
    try:
        claim = DisputeClaim(
            dispute_message=req.text,
            claimed_transaction_id=req.selected_transaction_id,
            claimed_amount=req.claimed_amount,
            claimed_date=req.claimed_date,
            claimed_merchant=req.claimed_merchant,
        )
        response = await copilot.run_async(
            case_id,
            req.actor_id,
            req.text,
            req.role,
            idempotency_key=idempotency_key,
            claim=claim,
        )
        return response.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/cases/{case_id}/audit")
def audit(case_id: str):
    if not copilot.db.get_case(case_id):
        raise HTTPException(404, "CASE_NOT_FOUND")
    return copilot.db.get_audit(case_id)


@app.get("/api/reviews/{task_id}")
def get_review(task_id: str):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    return dict(row)


@app.post("/api/reviews/{task_id}/claim")
def claim(task_id: str, req: ClaimRequest):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    try:
        principal = copilot.s.principal(req.reviewer_id, Role.reviewer)
        copilot.s.authorize(principal, row["case_id"])
    except Exception:
        raise HTTPException(403, "REVIEW_ACCESS_DENIED")
    if not copilot.db.claim_review(task_id, req.reviewer_id, req.expected_version):
        raise HTTPException(409, "REVIEW_CONFLICT")
    return {
        "status": "claimed",
        "task_id": task_id,
        "reviewer_id": req.reviewer_id,
        "version": req.expected_version + 1,
    }


@app.post("/api/reviews/{task_id}/resolve")
async def resolve(task_id: str, req: ResolveRequest):
    row = copilot.db.get_review(task_id)
    if not row:
        raise HTTPException(404, "REVIEW_NOT_FOUND")
    try:
        principal = copilot.s.principal(req.reviewer_id, Role.reviewer)
        copilot.s.authorize(principal, row["case_id"])
    except Exception:
        raise HTTPException(403, "REVIEW_ACCESS_DENIED")
    if not copilot.db.resolve_review(
        task_id,
        req.reviewer_id,
        req.expected_version,
        row["case_id"],
        req.action,
        req.reason_code,
        req.note,
    ):
        raise HTTPException(409, "REVIEW_CONFLICT")
    try:
        await copilot.resume_review(task_id, req.reviewer_id)
    except Exception:
        # The committed resolution is authoritative; reconcile can resume the workflow later.
        pass
    return {
        "status": "resolved",
        "case_id": row["case_id"],
        "task_id": task_id,
        "action": req.action,
    }


@app.post("/api/outbox/drain")
def drain():
    return {"delivered": OutboxWorker(copilot.db, copilot.settings.outbox_sink).deliver_once()}


@app.get("/api/rag/search")
def rag(q: str):
    return copilot.s.rag.search(q)
