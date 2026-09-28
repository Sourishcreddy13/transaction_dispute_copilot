from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import yaml


class PolicyRAG:
    """Synthetic chargeback-policy retrieval with Chroma + local deterministic fallback."""

    def __init__(self, persist_dir="./runtime/chroma", embedding_model="sentence-transformers/all-MiniLM-L6-v2", mode="chroma"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.embedding_model_name = embedding_model
        self.client = None
        self.collection = None
        self.embedder = None
        self.docs: list[tuple[str, str]] = []
        cfg_path = Path("config/rag.yaml")
        self.cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {"top_k": 3, "distance_threshold": 0.80}
        self.manifest_hash: str | None = None
        if mode == "chroma":
            try:
                import chromadb
                from sentence_transformers import SentenceTransformer

                self.client = chromadb.PersistentClient(path=str(self.persist_dir))
                self.collection = self.client.get_or_create_collection(
                    "dispute_policy", metadata={"hnsw:space": "cosine"}
                )
                self.embedder = SentenceTransformer(embedding_model)
            except Exception:
                self.mode = "local"
                self._load_local()
        else:
            self._load_local()

    def _load_local(self):
        corpus = Path("data/policy_corpus")
        self.docs = [(str(p), p.read_text(encoding="utf-8")) for p in sorted(corpus.glob("*.md"))]
        self.manifest_hash = hashlib.sha256(
            "".join(hashlib.sha256(text.encode()).hexdigest() for _, text in self.docs).encode()
        ).hexdigest()

    def _embed(self, texts):
        return self.embedder.encode(texts, normalize_embeddings=True).tolist()

    def index_directory(self, directory="data/policy_corpus"):
        docs = [(str(p), p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*.md"))]
        self.manifest_hash = hashlib.sha256(
            "".join(hashlib.sha256(text.encode()).hexdigest() for _, text in docs).encode()
        ).hexdigest()
        if self.mode != "chroma":
            self.docs = docs
            return len(docs)
        ids = [hashlib.sha256(text.encode()).hexdigest()[:24] for _, text in docs]
        if ids:
            self.collection.upsert(
                ids=ids,
                documents=[text for _, text in docs],
                metadatas=[{"source": source, "content_hash": hashlib.sha256(text.encode()).hexdigest()} for source, text in docs],
                embeddings=self._embed([text for _, text in docs]),
            )
        return len(docs)

    def search(self, query, k=None):
        k = k or int(self.cfg.get("top_k", 3))
        threshold = float(self.cfg.get("distance_threshold", 0.80))
        if self.mode == "chroma":
            result = self.collection.query(query_embeddings=self._embed([query]), n_results=k)
            out = []
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            dists = result.get("distances", [[]])[0]
            for i, doc in enumerate(docs):
                distance = dists[i] if i < len(dists) else None
                if distance is not None and distance > threshold:
                    continue
                out.append({
                    "source": metas[i]["source"],
                    "content": doc,
                    "distance": distance,
                    "content_hash": metas[i]["content_hash"],
                })
            return out

        q = set(re.findall(r"\w+", query.lower()))
        scored = []
        for source, text in self.docs:
            words = re.findall(r"\w+", text.lower())
            score = sum(1 for w in q if w in words) / (math.sqrt(len(q) * len(set(words))) or 1)
            scored.append((score, source, text))
        return [
            {
                "source": source,
                "content": text,
                "distance": 1 - score,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            }
            for score, source, text in sorted(scored, reverse=True)[:k]
            if score > 0
        ]
