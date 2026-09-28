def test_policy_rag_local():
 from app.core.rag import PolicyRAG
 r=PolicyRAG('./runtime/test-rag',mode='local'); r.index_directory(); hits=r.search('unauthorized provisional credit')
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
