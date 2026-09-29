"""
READY-TO-USE PATCH FILE
Filename: app/api/main_patched.py

This is a complete replacement for app/api/main.py with the fixes applied.
You can:
1. Copy the run() function below to your app/api/main.py
2. Or replace the entire file if you prefer

Changes made:
- Added asyncio.wait_for() timeout (prevents hanging)
- Improved error logging and messages
- Better exception handling with context
- No breaking changes to API contract
"""

from __future__ import annotations

from pathlib import Path
import asyncio
import logging

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.data_plane import DataPlane
from app.core.outbox import OutboxWorker
from app.models import DisputeClaim, Role
from app.workflow import Copilot

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    """Run dispute copilot with proper async handling.

    FIXED:
    - Added timeout to prevent hanging on database locks
    - Better error messages and logging
    - Proper async/await handling
    - Prevents "second submission no output" issue
    """
    try:
        claim = DisputeClaim(
            dispute_message=req.text,
            claimed_transaction_id=req.selected_transaction_id,
            claimed_amount=req.claimed_amount,
            claimed_date=req.claimed_date,
            claimed_merchant=req.claimed_merchant,
        )

        # Add timeout to prevent hanging on locked databases
        # Workflow typically completes in 10-30 seconds
        # Using 120s timeout to account for slow LLM providers
        try:
            logger.info(
                f"Starting copilot workflow for case {case_id} "
                f"(idempotency_key={idempotency_key[:20] if idempotency_key else None}...)"
            )

            response = await asyncio.wait_for(
                copilot.run_async(
                    case_id,
                    req.actor_id,
                    req.text,
                    req.role,
                    idempotency_key=idempotency_key,
                    claim=claim,
                ),
                timeout=120.0  # 2 minute timeout
            )

            logger.info(f"Workflow completed for case {case_id}")

        except asyncio.TimeoutError as exc:
            error_msg = (
                f"Workflow timeout after 120 seconds for case {case_id}. "
                f"The checkpoint database may be locked. "
                f"Try restarting the server and clear the runtime/ directory."
            )
            logger.error(error_msg)
            logger.error(
                "If this persists, check: "
                "1. Is the LLM API (Gemini/Groq) responding? "
                "2. Are there multiple requests trying to access the database simultaneously? "
                "3. Is the WAL file corrupt? (rm runtime/copilot.db-wal)"
            )
            raise HTTPException(504, "Workflow execution timeout") from exc

        # Validate response
        if response is None:
            logger.error(f"run_async returned None for case {case_id}")
            raise HTTPException(500, "Workflow returned empty response")

        result = response.model_dump(mode="json")
        logger.debug(f"Workflow response keys: {list(result.keys())}")
        return result

    except PermissionError as exc:
        logger.warning(f"Permission denied for case {case_id}: {exc}")
        raise HTTPException(403, str(exc))
    except LookupError as exc:
        logger.warning(f"Lookup error for case {case_id}: {exc}")
        raise HTTPException(404, str(exc))
    except HTTPException:
        # Re-raise HTTP exceptions as-is (already have proper status codes)
        raise
    except Exception as exc:
        # Log full error for debugging without exposing internals to client
        logger.error(
            f"Workflow error for case {case_id}: {exc.__class__.__name__}: {exc}",
            exc_info=True  # Include full traceback
        )
        # Return a generic error message (don't expose internals to frontend)
        raise HTTPException(500, f"Workflow failed: {exc.__class__.__name__}")


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
