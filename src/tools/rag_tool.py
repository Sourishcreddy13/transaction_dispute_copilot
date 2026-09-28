from __future__ import annotations

import os
import re
from typing import Any

from app.core.rag import PolicyRAG


class AgenticPolicyRAG:
    """Bounded retrieval-in-the-loop over the committed synthetic policy corpus."""

    def __init__(
        self,
        persist_dir: str,
        embedding_model: str,
        mode: str = "chroma",
        semantic_gateway: Any | None = None,
    ) -> None:
        self.rag = PolicyRAG(persist_dir, embedding_model, mode)
        self.semantic_gateway = semantic_gateway
        if mode in ("chroma", "local"):
            self.rag.index_directory("data/policy_corpus")

    def search(self, query: str, k: int = 3, run_id: str | None = None) -> list[dict]:
        attempts = [query, self._widen(query)]

        for current_query in attempts:
            hits = self.rag.search(current_query, k)
            if hits:
                return hits

        if self.semantic_gateway is not None and os.getenv("RAG_LLM_REWRITE", "0") == "1":
            try:
                rewrite = self.semantic_gateway.rewrite_query(query, run_id=run_id)
                return self.rag.search(rewrite.rewritten_query, k)
            except Exception:
                pass

        return []

    @staticmethod
    def _widen(query: str) -> str:
        synonyms = {
            "unauthorized": "unauthorised fraud card-not-present",
            "merchant": "merchant duplicate incorrect charge",
            "chargeback": "chargeback dispute network rule",
            "credit": "provisional credit temporary credit",
            "denied": "rejected abuse review",
        }
        lowered = query.lower()
        extra = " ".join(v for key, v in synonyms.items() if key in lowered)
        return re.sub(r"\s+", " ", f"{query} {extra}").strip()
