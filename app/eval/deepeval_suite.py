"""Qualitative DeepEval evaluation.

Decision correctness is owned by deterministic hidden/reference assertions. The LLM judge only evaluates
semantic properties and never receives expected actions, expected labels, or policy-selection gold answers.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# `deepeval` is a required (non-optional) dependency of this project (see
# pyproject.toml), so importing its base class at module level here is safe
# and doesn't introduce a new failure mode.
from deepeval.models import DeepEvalBaseLLM

# Real, verified bug (found live): this judge model has its OWN Gemini/Groq
# clients, entirely separate from `app/core/semantic.py`'s `SemanticGateway`
# -- so that module's timeout hardening (README bug 24) never covered this
# path at all. Confirmed for real: a genuine full-pipeline run recorded a
# single Gemini classify() call (a *different* call site, but the same
# underlying Gemini API and the same missing-timeout class of bug) that took
# `"latency_ms": 602575.79` -- just over 10 minutes -- to return, and this
# exact module's own `generate()`/`a_generate()` calls had no bound on how
# long they could block either. Reusing the same timeout primitive
# `app/core/semantic.py` already verified, rather than duplicating it, so a
# stuck Gemini or Groq call here now also fails over instead of hanging.
from app.core.semantic import PROVIDER_TIMEOUT_S, _invoke_with_deadline


def _is_fallback_worthy(exc: Exception) -> bool:
    """Return True when switching providers is preferable to retrying Gemini."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    text = str(exc)

    tokens = (
        "RESOURCE_EXHAUSTED",
        "429",
        "rate limit",
        "RateLimit",
        "ratelimit",
        "quota",
        "Quota",
        "503",
        "502",
        "500",
        "UNAVAILABLE",
        "temporarily unavailable",
        "ClientConnectorError",
        "ClientConnectorDNSError",
        "ClientConnectorConnectionError",
        "NameResolutionError",
        "nodename nor servname provided",
        "Temporary failure in name resolution",
        "Connection refused",
        "connection reset",
        "connection aborted",
        "Cannot connect to host",
    )

    return any(token in text for token in tokens)


class GeminiWithGroqFallback(DeepEvalBaseLLM):
    """LLM-judge model for DeepEval's metrics that mirrors this project's own
    documented Gemini-primary/Groq-fallback policy (see docs/deviations.md,
    D-01), instead of leaving the judge as a single point of failure.

    `app/core/semantic.py`'s `SemanticGateway` already does real Gemini->Groq
    fallback, but only for the main dispute-resolution pipeline's own calls
    (classify/rewrite) — it has no connection to DeepEval's qualitative judge,
    which previously instantiated a bare `deepeval.models.GeminiModel` with no
    fallback wired in at all. That's why a Gemini 429 here used to just kill
    the whole eval run instead of failing over, even though the rest of the
    app already knows how to fail over to Groq.

    Must actually subclass `DeepEvalBaseLLM` (not just duck-type it): DeepEval's
    `deepeval.metrics.utils.models._build_model` does a real `isinstance(model,
    DeepEvalBaseLLM)` check and raises `TypeError` for anything else — confirmed
    by constructing this for real against the installed `deepeval` package
    (a plain duck-typed class fails that check). It's deliberately not one of
    DeepEval's "native" provider classes (see `is_native_model`), so
    `using_native_model` is False for metrics built with it, and DeepEval reads
    results as plain (schema-instance, cost) tuples/JSON from any non-native
    model — see `deepeval.metrics.utils.generation.a_generate_with_schema_and_extract`.
    """

    def __init__(self, gemini_model: str, groq_model: str, google_api_key: str, groq_api_key: Optional[str]):
        from deepeval.models import GeminiModel

        self._gemini = GeminiModel(model=gemini_model, api_key=google_api_key, temperature=0)
        self._groq_model_name = groq_model
        self._groq_api_key = groq_api_key
        self._groq_client = None
        super().__init__(f"{gemini_model} (fallback: groq/{groq_model})")

    def load_model(self):
        return self._gemini

    def get_model_name(self) -> str:
        return self.name

    def _groq(self):
        if self._groq_client is None:
            from langchain_groq import ChatGroq

            key = self._groq_api_key or os.getenv("GROQ_API_KEY")
            if not key:
                raise RuntimeError(
                    "Gemini judge call failed and GROQ_API_KEY is not set, so there is "
                    "no fallback provider to use. Set GROQ_API_KEY in .env to enable it."
                )
            self._groq_client = ChatGroq(model=self._groq_model_name, temperature=0, groq_api_key=key)
        return self._groq_client

    def _warn_fallback(self, exc: Exception) -> None:
        print(
            f"[deepeval judge] Gemini call failed ({exc.__class__.__name__}: {str(exc)[:200]}); "
            f"falling back to Groq model '{self._groq_model_name}' (docs/deviations.md D-01).",
            file=sys.stderr,
        )

    def generate(self, prompt: str, schema: Any = None):
        try:
            # Hard deadline around the actual blocking call -- see the
            # module-level comment on PROVIDER_TIMEOUT_S/_invoke_with_deadline
            # above for the real 602-second hang that motivated this.
            return _invoke_with_deadline(self._gemini.generate, prompt, schema=schema, timeout=PROVIDER_TIMEOUT_S)
        except Exception as exc:
            if not _is_fallback_worthy(exc):
                raise
            self._warn_fallback(exc)
            client = self._groq()
            if schema is not None:
                # method="json_schema", not langchain_groq's own default
                # ("function_calling"): confirmed for real against the live
                # Groq API, using DeepEval's own actual generate_statements
                # prompt (few-shot example + trailing "JSON:" cue) and its real
                # Statements schema, that "function_calling" fails
                # deterministically (3/3) with `groq.BadRequestError: ...
                # attempted to call tool 'json' which was not in request.tools`
                # -- gpt-oss-120b hallucinates a self-invented "json" tool call
                # instead of using the one real tool it was bound to. Every
                # DeepEval metric calls its judge with a schema shaped this
                # way, so this isn't a one-off prompt quirk. method="json_schema"
                # (Groq's native structured-outputs mode, no forced tool
                # calling involved) succeeded 0/3 failures across repeated
                # real calls with the identical prompt/schema. See the same
                # fix and fuller detail in app/core/semantic.py's RealProvider.
                structured = client.with_structured_output(schema, method="json_schema", include_raw=False)
                return _invoke_with_deadline(structured.invoke, prompt, timeout=PROVIDER_TIMEOUT_S), 0.0
            return _invoke_with_deadline(client.invoke, prompt, timeout=PROVIDER_TIMEOUT_S).content, 0.0

    async def a_generate(self, prompt: str, schema: Any = None):
        try:
            # asyncio.wait_for, not the thread-based _invoke_with_deadline:
            # this call is already a coroutine, so this is the natural
            # equivalent (same primitive src/mcp_client.py's MCP calls use).
            return await asyncio.wait_for(self._gemini.a_generate(prompt, schema=schema), timeout=PROVIDER_TIMEOUT_S)
        except Exception as exc:
            if not _is_fallback_worthy(exc):
                raise
            self._warn_fallback(exc)
            client = self._groq()
            if schema is not None:
                # See the matching comment in generate() above.
                structured = client.with_structured_output(schema, method="json_schema", include_raw=False)
                return await asyncio.wait_for(structured.ainvoke(prompt), timeout=PROVIDER_TIMEOUT_S), 0.0
            result = await asyncio.wait_for(client.ainvoke(prompt), timeout=PROVIDER_TIMEOUT_S)
            return result.content, 0.0


def build_metrics():
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric

    # "gemini-2.5-pro" was retired for new API keys (Google now returns a 404
    # telling callers to move to "gemini-3.1-pro-preview" or later); default to
    # that instead. Override with GEMINI_JUDGE_MODEL if Google retires this one
    # too, or if you want a cheaper/faster judge model.
    #
    # The judge now also fails over to Groq on quota/rate-limit/5xx errors,
    # same as the main pipeline's SemanticGateway (docs/deviations.md D-01).
    # Set EVAL_JUDGE_FALLBACK=0 to use a bare Gemini judge with no fallback.
    if os.getenv("EVAL_JUDGE_FALLBACK", "1") == "1":
        model = GeminiWithGroqFallback(
            gemini_model=os.getenv("GEMINI_JUDGE_MODEL") or "gemini-3.1-pro-preview",
            # `or`, not a nested os.getenv default: .env.example sets
            # GROQ_JUDGE_MODEL="" (empty, meaning "use GROQ_MODEL") so it's
            # discoverable there, and os.getenv treats a SET-but-empty var as
            # present, returning "" rather than falling through to a default.
            groq_model=os.getenv("GROQ_JUDGE_MODEL") or os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b",
            google_api_key=os.environ["GOOGLE_API_KEY"],
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
        )
    else:
        from deepeval.models import GeminiModel

        model = GeminiModel(
            model=os.getenv("GEMINI_JUDGE_MODEL") or "gemini-3.1-pro-preview",
            api_key=os.environ["GOOGLE_API_KEY"],
            temperature=0,
        )
    return [
        AnswerRelevancyMetric(threshold=0.8, model=model),
        FaithfulnessMetric(threshold=0.8, model=model),
        HallucinationMetric(threshold=0.8, model=model),
    ]


def run_qualitative_eval() -> list[dict]:
    from deepeval.test_case import LLMTestCase
    from app.workflow import Copilot

    root = Path(__file__).resolve().parents[2]
    cases = json.loads((root / "data/eval/cases.json").read_text(encoding="utf-8"))
    cop = Copilot()
    metrics = build_metrics()
    results = []

    for item in cases:
        case_id = cop.db.create_case(item["customer_id"], "analyst:A-001", item.get("transaction_id"))
        out = cop.run(case_id, "analyst:A-001", item["text"])
        context = [h.get("content", "") for h in out.analyst_view.get("policy_hits", [])]

        result = {"id": item["id"]}

        # Real, verified bug: `out.customer_view` is legitimately empty for
        # any case that lands in human review (see `data/eval/cases.json`'s
        # "E2", `expected_human_review: true` -- the graph defers the
        # customer-facing message until a reviewer resolves the case, so
        # `RunResponse.customer_view` comes back `""` by design, confirmed
        # against this project's own `app/workflow.py`, not a bug in the
        # workflow itself). DeepEval's `AnswerRelevancyMetric.measure()`
        # (and the other two LLM-judge metrics) refuse to score an empty
        # `actual_output` at all: `deepeval.errors.MissingTestCaseParamsError:
        # 'actual_output' cannot be empty for the 'Answer Relevancy' metric`
        # -- a real crash hit for real, after the Gemini->Groq fallback
        # (bug #17) had already worked correctly for every earlier case.
        # There is genuinely nothing for an "answer relevancy" / "faithfulness"
        # / "hallucination" judge to score here -- the customer hasn't been
        # shown anything yet -- so this is skipped rather than judged, and
        # recorded as such instead of silently omitted or fabricated.
        if not out.customer_view:
            result["skipped"] = "no customer-facing output yet (case pending human review); nothing for the LLM judge to score"
            results.append(result)
            continue

        # The evaluator sees only the user input, system output and supporting evidence.
        # Expected decision labels are intentionally excluded.
        test_case = LLMTestCase(
            input=item["text"],
            actual_output=out.customer_view,
            context=context,
            retrieval_context=context,
        )
        for metric in metrics:
            metric.measure(test_case)
            # deepeval's metric classes (AnswerRelevancyMetric,
            # FaithfulnessMetric, HallucinationMetric -- confirmed against the
            # real installed deepeval==4.2.6 package) do NOT have a plain
            # `.name` attribute. They define `__name__` as a @property
            # instead (returning a human-readable label like "Answer
            # Relevancy"), so `metric.name` raises AttributeError. This was a
            # real crash hit at the very end of a live run, after all three
            # metrics (including a real Gemini->Groq fallback) had already
            # measured successfully. Verified for real: `metric.__name__`
            # returns a plain string ("Answer Relevancy" / "Faithfulness" /
            # "Hallucination"), which is a valid JSON dict key.
            result[metric.__name__] = {
                "score": metric.score,
                "reason": metric.reason,
            }
        results.append(result)

    output = root / "reports/deepeval_qualitative.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


if __name__ == "__main__":
    print(json.dumps(run_qualitative_eval(), indent=2, default=str))
