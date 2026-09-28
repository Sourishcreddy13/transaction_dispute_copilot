"""Regression test for a real crash in `run_qualitative_eval`.

`app/eval/deepeval_suite.py`'s `run_qualitative_eval()` used to build its
per-case result dict with `result[metric.name] = {...}`. That crashed for
real, at the very end of a live `generate_evidence.py` run (after all three
metrics -- including a real Gemini->Groq fallback -- had already measured
successfully):

    AttributeError: 'AnswerRelevancyMetric' object has no attribute 'name'

Confirmed against the real installed `deepeval==4.2.6` package: its metric
classes (`AnswerRelevancyMetric`, `FaithfulnessMetric`, `HallucinationMetric`)
define `__name__` as a `@property` (returning a human-readable label such as
"Answer Relevancy"), not a plain `.name` attribute. `build_golden_signals.py`
already expects exactly those human-readable keys ("Answer Relevancy",
"Faithfulness", "Hallucination"), so `metric.__name__` is also the key format
the rest of the pipeline was already relying on.

This test never touches the network, Copilot's real graph, or a real
deepeval/Gemini/Groq model: `Copilot` and `build_metrics` are monkeypatched
with lightweight fakes so it runs fast and deterministically under pytest.
"""
from __future__ import annotations

import json
import types

import pytest

from app.eval import deepeval_suite


class _FakeMetric:
    """Stands in for a real deepeval metric instance. Deliberately exposes
    `__name__` as a property and NOT a `.name` attribute, matching the real
    `AnswerRelevancyMetric`/`FaithfulnessMetric`/`HallucinationMetric`
    classes -- accessing `.name` on this must raise AttributeError, exactly
    like the real bug."""

    def __init__(self, label: str, score: float, reason: str):
        self._label = label
        self.score = score
        self.reason = reason
        self.measured_with: list = []

    @property
    def __name__(self):
        return self._label

    def measure(self, test_case):
        self.measured_with.append(test_case)


class _FakeCopilotResult:
    def __init__(self, customer_view: str):
        self.customer_view = customer_view
        self.analyst_view = {"policy_hits": [{"content": "some policy excerpt"}]}


class _FakeDb:
    def create_case(self, customer_id, actor, transaction_id):
        return "CASE-FAKE-1"


class _FakeCopilot:
    def __init__(self):
        self.db = _FakeDb()

    def run(self, case_id, actor, text):
        # "PENDING" is this fixture's stand-in for a case that landed in
        # human review, where `RunResponse.customer_view` legitimately comes
        # back "" (see app/workflow.py) because no customer-facing message
        # has been produced yet.
        if "PENDING" in text:
            return _FakeCopilotResult(customer_view="")
        return _FakeCopilotResult(customer_view=f"handled: {text}")


def test_run_qualitative_eval_uses_dunder_name_not_dot_name(monkeypatch, tmp_path):
    """The real bug: `metric.name` doesn't exist on deepeval's metric
    classes. This must not raise AttributeError, and the result dict's keys
    must be the metrics' human-readable `__name__` values."""
    fake_metrics = [
        _FakeMetric("Answer Relevancy", 0.95, "relevant"),
        _FakeMetric("Faithfulness", 0.88, "faithful"),
        _FakeMetric("Hallucination", 0.02, "no hallucination"),
    ]

    monkeypatch.setattr(deepeval_suite, "build_metrics", lambda: fake_metrics)
    monkeypatch.setattr("app.workflow.Copilot", _FakeCopilot)

    # Redirect the report write into a throwaway directory instead of the
    # real repo's reports/ folder.
    fake_root = tmp_path
    (fake_root / "data" / "eval").mkdir(parents=True)
    (fake_root / "data" / "eval" / "cases.json").write_text(
        json.dumps([{"id": "E1", "customer_id": "C-1", "transaction_id": "T-1", "text": "test dispute text"}]),
        encoding="utf-8",
    )
    # `run_qualitative_eval` derives its repo root from
    # `Path(__file__).resolve().parents[2]`; pointing `__file__` at a
    # not-necessarily-existing path under `fake_root/app/eval/` redirects it
    # there (pathlib's `resolve()` doesn't require the path to exist).
    monkeypatch.setattr(deepeval_suite, "__file__", str(fake_root / "app" / "eval" / "deepeval_suite.py"))

    results = deepeval_suite.run_qualitative_eval()

    assert len(results) == 1
    result = results[0]
    assert result["id"] == "E1"
    # This is the actual regression check: these must be the metrics'
    # __name__ values (human-readable labels), never AttributeError, and
    # never the class name ("AnswerRelevancyMetric").
    assert result["Answer Relevancy"] == {"score": 0.95, "reason": "relevant"}
    assert result["Faithfulness"] == {"score": 0.88, "reason": "faithful"}
    assert result["Hallucination"] == {"score": 0.02, "reason": "no hallucination"}

    # The report file was actually written (to the redirected fake root).
    written = json.loads((fake_root / "reports" / "deepeval_qualitative.json").read_text(encoding="utf-8"))
    assert written == results


def test_run_qualitative_eval_skips_cases_with_no_customer_facing_output(monkeypatch, tmp_path):
    """Real crash this guards against: `data/eval/cases.json` includes a case
    with `expected_human_review: true` ("E2"), for which the real app's
    `RunResponse.customer_view` legitimately comes back `""` (the graph
    defers the customer-facing message until a reviewer resolves the case --
    confirmed against app/workflow.py, not a bug there). DeepEval's
    `AnswerRelevancyMetric.measure()` refuses to score an empty
    `actual_output` and raises `MissingTestCaseParamsError` -- a real crash
    hit for real, after the Gemini->Groq fallback had already worked
    correctly for the earlier cases in the same run. This must be skipped,
    not crash, and the skip must be visible in the report rather than the
    case silently vanishing."""
    fake_metrics = [_FakeMetric("Answer Relevancy", 0.95, "relevant")]

    monkeypatch.setattr(deepeval_suite, "build_metrics", lambda: fake_metrics)
    monkeypatch.setattr("app.workflow.Copilot", _FakeCopilot)

    fake_root = tmp_path
    (fake_root / "data" / "eval").mkdir(parents=True)
    (fake_root / "data" / "eval" / "cases.json").write_text(
        json.dumps(
            [
                {"id": "E1", "customer_id": "C-1", "transaction_id": "T-1", "text": "a normal dispute"},
                {"id": "E2", "customer_id": "C-2", "transaction_id": "T-2", "text": "PENDING review case"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(deepeval_suite, "__file__", str(fake_root / "app" / "eval" / "deepeval_suite.py"))

    results = deepeval_suite.run_qualitative_eval()

    assert len(results) == 2
    scored, skipped = results
    assert scored["id"] == "E1"
    assert scored["Answer Relevancy"] == {"score": 0.95, "reason": "relevant"}
    # The metric must never have been asked to measure the pending case.
    assert len(fake_metrics[0].measured_with) == 1

    assert skipped["id"] == "E2"
    assert "skipped" in skipped
    assert "Answer Relevancy" not in skipped


def test_fake_metric_name_attribute_access_raises_like_the_real_bug():
    """Characterizes the real upstream shape this test suite guards against:
    `.name` must NOT exist on a deepeval-style metric (only `__name__`)."""
    metric = _FakeMetric("Answer Relevancy", 1.0, "ok")
    with pytest.raises(AttributeError):
        metric.name
