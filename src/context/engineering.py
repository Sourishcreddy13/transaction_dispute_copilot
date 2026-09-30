from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any


@dataclass
class ContextEnvelope:
    masked_text: str
    quarantined: bool
    injection_flag: bool
    working_facts: dict[str, Any] = field(default_factory=dict)
    conversation_summary: str | None = None


class ContextEngineer:
    """Write/select/compress/isolate boundary with hard size limits."""

    INJECTION = re.compile(
        r"(ignore\s+(all|previous)\s+instructions|system\s+prompt|developer\s+message|"
        r"reveal\s+(the|my)\s+(account|pan|secret)|jailbreak|another\s+customer|other\s+customer)",
        re.I,
    )

    MAX_FACTS = 50
    MAX_TEXT_CHARS = 24_000

    def write(self, env: ContextEnvelope, facts: dict[str, Any]) -> ContextEnvelope:
        for key, value in facts.items():
            if len(env.working_facts) >= self.MAX_FACTS:
                break
            if isinstance(value, (str, int, float, bool)) or value is None:
                env.working_facts[str(key)[:100]] = value
        return env

    def isolate(self, masked_text: str) -> ContextEnvelope:
        bounded = str(masked_text)[: self.MAX_TEXT_CHARS]
        return ContextEnvelope(
            masked_text=bounded,
            quarantined=True,
            injection_flag=bool(self.INJECTION.search(bounded)),
        )

    def select(self, env: ContextEnvelope, facts: dict[str, Any]) -> ContextEnvelope:
        env.working_facts = {
            str(k)[:100]: v for k, v in list(facts.items())[: self.MAX_FACTS]
            if isinstance(v, (str, int, float, bool)) or v is None
        }
        return env

    def summarize(self, text: str, max_chars: int = 2800) -> str:
        max_chars = max(100, min(int(max_chars), self.MAX_TEXT_CHARS))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            return text[:max_chars]
        ranked = sorted(
            sentences,
            key=lambda s: (
                int(bool(re.search(r"transaction|charge|merchant|amount|date|unauthori|fraud|account", s, re.I))),
                len(s),
            ),
            reverse=True,
        )
        summary = " ".join(ranked)
        return summary[: max_chars - 3] + "..." if len(summary) > max_chars else summary

    def compress(self, env: ContextEnvelope, max_chars: int = 7200, max_tokens: int = 1800) -> ContextEnvelope:
        max_chars = max(200, min(int(max_chars), self.MAX_TEXT_CHARS))
        max_tokens = max(64, int(max_tokens))
        estimated_tokens = math.ceil(len(env.masked_text) / 4)
        if len(env.masked_text) <= max_chars and estimated_tokens <= max_tokens:
            return env
        char_budget = min(max_chars, max_tokens * 4)
        env.conversation_summary = self.summarize(env.masked_text, max_chars=min(2800, char_budget))
        env.masked_text = env.conversation_summary
        return env
