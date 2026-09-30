from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PIIResult:
    masked_text: str
    entities: list[dict]
    engine: str


class PIIService:
    """PII ingress gate; security-mode failures are fail-closed."""

    def __init__(self, mode: str = "presidio"):
        self.requested_mode = mode
        self.mode = mode
        self.analyzer = None
        self.anonymizer = None
        self.initialization_error: str | None = None
        if mode == "presidio":
            try:
                from presidio_analyzer import AnalyzerEngine
                from presidio_anonymizer import AnonymizerEngine

                self.analyzer = AnalyzerEngine()
                self.anonymizer = AnonymizerEngine()
            except Exception as exc:  # noqa: BLE001
                self.initialization_error = type(exc).__name__
                self.mode = "presidio_unavailable"
        elif mode != "regex":
            raise ValueError("UNSUPPORTED_PII_MODE")

    def scan(self, text: str) -> PIIResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("PII_INPUT_EMPTY")
        if len(text) > 5000:
            raise ValueError("PII_INPUT_TOO_LARGE")

        if self.mode == "presidio" and self.analyzer and self.anonymizer:
            try:
                results = self.analyzer.analyze(text=text, language="en")
                replacements = {
                    r.entity_type: f"<REDACTED_{r.entity_type}>" for r in results
                }
                from presidio_anonymizer.entities import OperatorConfig

                ops = {
                    k: OperatorConfig("replace", {"new_value": v})
                    for k, v in replacements.items()
                }
                masked = self.anonymizer.anonymize(
                    text=text, analyzer_results=results, operators=ops
                ).text
                return PIIResult(
                    masked,
                    [
                        {
                            "type": r.entity_type,
                            "start": r.start,
                            "end": r.end,
                            "score": r.score,
                        }
                        for r in results
                    ],
                    "presidio",
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("PII_SCAN_FAILED") from exc

        if self.mode == "presidio_unavailable":
            raise RuntimeError("PII_ENGINE_UNAVAILABLE")

        # Explicitly configured deterministic local mode only. This path is useful
        # for offline tests, not a silent downgrade from Presidio.
        patterns = {
            "PAN": r"\b(?:\d[ -]?){13,19}\b",
            "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            "PHONE": r"\b(?:\+?91[- ]?)?[6-9]\d{9}\b",
            "ACCOUNT": r"\b\d{8,20}\b",
        }
        masked = text
        entities: list[dict] = []
        for typ, pat in patterns.items():
            masked, count = re.subn(pat, f"<REDACTED_{typ}>", masked)
            if count:
                entities.append({"type": typ, "count": count})
        return PIIResult(masked, entities, "regex")
