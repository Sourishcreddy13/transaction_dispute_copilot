from __future__ import annotations

import hashlib
import json
import os
import math
import re
from pathlib import Path

import yaml


class PolicyRAG:
    """Synthetic chargeback-policy retrieval with pinned corpus provenance and bounded fallback."""

    def __init__(self, persist_dir="./runtime/chroma", embedding_model="sentence-transformers/all-MiniLM-L6-v2", mode="chroma"):
        self.project_root = Path(__file__).resolve().parents[2]
        persist = Path(persist_dir)
        self.persist_dir = (self.project_root / persist).resolve() if not persist.is_absolute() else persist.resolve()
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.embedding_model_name = embedding_model
        self.client = None
        self.collection = None
        self.embedder = None
        self.docs: list[tuple[str, str]] = []
        self._doc_terms: list[tuple[str, str, set[str]]] = []
        cfg_path = self.project_root / "config" / "rag.yaml"
        self.cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"top_k": 3, "distance_threshold": 0.80}
        self.manifest_hash: str | None = None
        if mode == "chroma":
            try:
                import chromadb
                from sentence_transformers import SentenceTransformer
                self.client = chromadb.PersistentClient(path=str(self.persist_dir))
                self.collection = self.client.get_or_create_collection("dispute_policy", metadata={"hnsw:space": "cosine"})
                self.embedder = SentenceTransformer(embedding_model)
                self.degraded_reason = None
            except Exception as exc:  # noqa: BLE001
                self.degraded_reason = type(exc).__name__
                if os.getenv("RAG_ALLOW_DEGRADED", "0") == "1":
                    self.mode = "local"
                    self._load_local()
                else:
                    raise RuntimeError("RAG_SEMANTIC_BACKEND_UNAVAILABLE") from exc
        else:
            self.degraded_reason = None
            self._load_local()

    @staticmethod
    def _manifest(docs: list[tuple[str, str]]) -> str:
        # Version the manifest so changing provenance representation (absolute
        # path -> repository-relative path) forces a metadata refresh.
        material = "RAG_MANIFEST_V2\n" + "\n".join(
            f"{Path(source).as_posix()}:{hashlib.sha256(text.encode()).hexdigest()}"
            for source, text in docs
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _repo_source(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return path.name

    def _load_local(self):
        corpus = self.project_root / "data" / "policy_corpus"
        self.docs = [(self._repo_source(p), p.read_text(encoding="utf-8")) for p in sorted(corpus.glob("*.md"))]
        self.manifest_hash = self._manifest(self.docs)
        self._doc_terms = [(source, text, set(re.findall(r"\w+", text.lower()))) for source, text in self.docs]

    def _embed(self, texts):
        return self.embedder.encode(texts, normalize_embeddings=True).tolist()

    def index_directory(self, directory="data/policy_corpus"):
        directory_path = Path(directory)
        if not directory_path.is_absolute():
            directory_path = self.project_root / directory_path
        docs = [(self._repo_source(p), p.read_text(encoding="utf-8")) for p in sorted(directory_path.glob("*.md"))]
        self.manifest_hash = self._manifest(docs)
        manifest_path = self.persist_dir / "index_manifest.json"
        previous = None
        if manifest_path.exists():
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = None
        if self.mode != "chroma":
            self.docs = docs
            self._doc_terms = [(source, text, set(re.findall(r"\w+", text.lower()))) for source, text in docs]
            return len(docs)
        if previous and previous.get("manifest_hash") == self.manifest_hash:
            return len(docs)
        ids = [hashlib.sha256(text.encode()).hexdigest()[:24] for _, text in docs]
        if ids:
            self.collection.upsert(
                ids=ids,
                documents=[text for _, text in docs],
                metadatas=[{"source": source, "content_hash": hashlib.sha256(text.encode()).hexdigest()} for source, text in docs],
                embeddings=self._embed([text for _, text in docs]),
            )
        manifest_path.write_text(json.dumps({"manifest_hash": self.manifest_hash, "document_count": len(docs)}, indent=2) + "\n", encoding="utf-8")
        return len(docs)

    def search(self, query, k=None, run_id=None):
        _ = run_id
        query = str(query).strip()
        if not query or len(query) > 1000:
            raise ValueError("RAG_QUERY_INVALID")
        k = max(1, min(int(k or self.cfg.get("top_k", 3)), 10))
        threshold = float(self.cfg.get("distance_threshold", 0.80))
        if self.mode == "chroma":
            try:
                result = self.collection.query(query_embeddings=self._embed([query]), n_results=k)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("RAG_VECTOR_SEARCH_FAILED") from exc
            out = []
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            dists = result.get("distances", [[]])[0]
            for i, doc in enumerate(docs):
                distance = dists[i] if i < len(dists) else None
                if distance is not None and distance > threshold:
                    continue
                meta = metas[i] or {}
                out.append({"source": meta.get("source", "unknown"), "content": str(doc)[:12000], "distance": distance, "content_hash": meta.get("content_hash") or hashlib.sha256(str(doc).encode()).hexdigest()})
            return out

        q = set(re.findall(r"\w+", query.lower()))
        scored = []
        for source, text, words in self._doc_terms:
            score = sum(1 for w in q if w in words) / (math.sqrt(len(q) * len(words)) or 1)
            scored.append((score, source, text))
        return [
            {"source": source, "content": text[:12000], "distance": 1 - score, "content_hash": hashlib.sha256(text.encode()).hexdigest()}
            for score, source, text in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]
            if score > 0
        ]
