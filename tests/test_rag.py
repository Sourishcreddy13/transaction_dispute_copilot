def test_policy_rag_local(tmp_path):
 from app.core.rag import PolicyRAG
 r=PolicyRAG(str(tmp_path / 'test-rag'),mode='local'); r.index_directory(); hits=r.search('unauthorized provisional credit')
 assert hits and 'unauthorized' in hits[0]['content'].lower()


def test_policy_rag_returns_committed_source(tmp_path):
    from pathlib import Path
    from app.core.rag import PolicyRAG
    r = PolicyRAG(str(tmp_path / "r"), mode="local")
    r.index_directory("data/policy_corpus")
    hits = r.search("unauthorized provisional credit", 2)
    assert hits
    assert all(Path(h["source"]).exists() for h in hits)
    assert all(h["content_hash"] for h in hits)

def test_rag_query_is_bounded(tmp_path):
    from app.core.rag import PolicyRAG
    import pytest
    rag=PolicyRAG(str(tmp_path / 'test-rag'), mode="local")
    rag.index_directory()
    assert rag.search("unauthorized chargeback", k=100)
    assert len(rag.search("unauthorized chargeback", k=100)) <= 10
    with pytest.raises(ValueError, match="RAG_QUERY_INVALID"): rag.search("x"*1001)


def test_rag_sources_are_repo_relative(tmp_path):
    from app.core.rag import PolicyRAG

    rag = PolicyRAG(persist_dir=str(tmp_path), mode="local")
    rows = rag.search("unauthorized transaction")
    assert rows
    assert all(not str(r["source"]).startswith("/") for r in rows)
    assert all(not (len(str(r["source"])) > 2 and str(r["source"])[1] == ":") for r in rows)
