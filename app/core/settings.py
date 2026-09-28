from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()

@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("DB_PATH", "./runtime/copilot.db")
    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "./runtime/checkpoints.db")
    access_secret: str = os.getenv("ACCESS_SECRET", "dev-only-change-me")
    semantic_mode: str = os.getenv("SEMANTIC_MODE", "real")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    rag_mode: str = os.getenv("RAG_MODE", "chroma")
    rag_path: str = os.getenv("RAG_PATH", "./runtime/chroma")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    memory_path: str = os.getenv("MEMORY_PATH", "./runtime/memory.db")
    pii_mode: str = os.getenv("PII_MODE", "presidio")
    otel_enabled: bool = os.getenv("OTEL_ENABLED", "true").lower() == "true"
    phoenix_endpoint: str = os.getenv("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
    outbox_sink: str = os.getenv("OUTBOX_SINK", "./runtime/outbox-deliveries.jsonl")
    max_provider_calls: int = int(os.getenv("MAX_PROVIDER_CALLS", "8"))
    max_run_seconds: int = int(os.getenv("MAX_RUN_SECONDS", "120"))
    max_graph_steps: int = int(os.getenv("MAX_GRAPH_STEPS", "40"))
    classification_budget: int = int(os.getenv("CLASSIFICATION_BUDGET", "2"))
    context_compression_budget: int = int(os.getenv("CONTEXT_COMPRESSION_BUDGET", "1"))
    rag_rewrite_budget: int = int(os.getenv("RAG_REWRITE_BUDGET", "1"))
    provider_max_attempts: int = int(os.getenv("PROVIDER_MAX_ATTEMPTS", "2"))
    high_value_threshold: str = os.getenv("HIGH_VALUE_THRESHOLD", "50000.00")
