from __future__ import annotations

import json
from pathlib import Path


def test_provider_pricing_config_is_provider_specific():
    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config/providers.yaml").read_text(encoding="utf-8"))
    assert config["providers"]["gemini"]["pricing"] == {
        "input_usd_per_1m_tokens": 0.25,
        "output_usd_per_1m_tokens": 1.50,
    }
    assert config["providers"]["groq"]["pricing"] == {
        "input_usd_per_1m_tokens": 0.15,
        "output_usd_per_1m_tokens": 0.60,
    }


def test_quality_report_keeps_skipped_cases_visible():
    root = Path(__file__).resolve().parents[1]
    report = root / "reports/deepeval_qualitative.json"
    if not report.exists():
        return
    items = json.loads(report.read_text(encoding="utf-8"))
    assert any(item.get("skipped") for item in items) or all(
        "Answer Relevancy" in item for item in items
    )
