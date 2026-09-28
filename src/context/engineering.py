from __future__ import annotations
from dataclasses import dataclass, field
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
    """Write/select/compress/isolate boundary. Raw claimant text never enters graph state."""
    INJECTION = re.compile(r"(ignore (all|previous) instructions|system prompt|developer message|reveal (the|my) (account|pan|secret)|jailbreak)", re.I)

    def write(self, env: ContextEnvelope, facts: dict[str, Any]) -> ContextEnvelope:
        """Persist only selected, structured working facts into the current turn envelope."""
        for key, value in facts.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                env.working_facts[key] = value
        return env
    def isolate(self, masked_text: str) -> ContextEnvelope:
        return ContextEnvelope(
            masked_text=masked_text,
            quarantined=True,
            injection_flag=bool(self.INJECTION.search(masked_text)),
        )
    def select(self, env: ContextEnvelope, facts: dict[str, Any]) -> ContextEnvelope:
        env.working_facts = dict(facts)
        return env
    def summarize(self, text: str, max_chars: int = 720) -> str:
        """Deterministic summarization middleware: preserve the highest-information sentences."""
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
        return summary[:max_chars - 3] + "..." if len(summary) > max_chars else summary

    def compress(self, env: ContextEnvelope, max_chars: int = 1800) -> ContextEnvelope:
        if len(env.masked_text) <= max_chars:
            return env
        env.conversation_summary = self.summarize(env.masked_text, max_chars=720)
        env.masked_text = env.conversation_summary
        return env
