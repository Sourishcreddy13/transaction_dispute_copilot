from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() == "true"


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: _str("DB_PATH", "./runtime/copilot.db"))
    checkpoint_path: str = field(default_factory=lambda: _str("CHECKPOINT_PATH", "./runtime/checkpoints.db"))
    access_secret: str = field(default_factory=lambda: _str("ACCESS_SECRET", "dev-only-change-me"))
    semantic_mode: str = field(default_factory=lambda: _str("SEMANTIC_MODE", "real"))
    gemini_model: str = field(default_factory=lambda: _str("GEMINI_MODEL", "gemini-3.1-flash-lite"))
    groq_model: str = field(default_factory=lambda: _str("GROQ_MODEL", "openai/gpt-oss-120b"))
    rag_mode: str = field(default_factory=lambda: _str("RAG_MODE", "chroma"))
    rag_path: str = field(default_factory=lambda: _str("RAG_PATH", "./runtime/chroma"))
    embedding_model: str = field(default_factory=lambda: _str("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    memory_path: str = field(default_factory=lambda: _str("MEMORY_PATH", "./runtime/memory.db"))
    pii_mode: str = field(default_factory=lambda: _str("PII_MODE", "presidio"))
    otel_enabled: bool = field(default_factory=lambda: _bool("OTEL_ENABLED", True))
    phoenix_endpoint: str = field(default_factory=lambda: _str("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces"))
    outbox_sink: str = field(default_factory=lambda: _str("OUTBOX_SINK", "./runtime/outbox-deliveries.jsonl"))
    max_provider_calls: int = field(default_factory=lambda: _int("MAX_PROVIDER_CALLS", 8))
    max_run_seconds: int = field(default_factory=lambda: _int("MAX_RUN_SECONDS", 120))
    max_graph_steps: int = field(default_factory=lambda: _int("MAX_GRAPH_STEPS", 40))
    classification_budget: int = field(default_factory=lambda: _int("CLASSIFICATION_BUDGET", 2))
    context_compression_budget: int = field(default_factory=lambda: _int("CONTEXT_COMPRESSION_BUDGET", 1))
    rag_rewrite_budget: int = field(default_factory=lambda: _int("RAG_REWRITE_BUDGET", 1))
    provider_max_attempts: int = field(default_factory=lambda: _int("PROVIDER_MAX_ATTEMPTS", 2))
    high_value_threshold: str = field(default_factory=lambda: _str("HIGH_VALUE_THRESHOLD", "50000.00"))
