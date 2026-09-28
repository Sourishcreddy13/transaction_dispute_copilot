import json
from pathlib import Path


def test_golden_cases_separate_action_and_review_expectations():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "data/eval/cases.json").read_text())
    e2 = next(x for x in cases if x["id"] == "E2")
    assert e2["expected_action"] == "provisional_credit"
    assert e2["expected_human_review"] is True


def test_evaluation_design_blinds_llm_judge():
    text = (Path(__file__).resolve().parents[1] / "docs/evaluation-design.md").read_text()
    assert "Expected actions and expected review states" in text
    assert "does not receive expected action" in text
