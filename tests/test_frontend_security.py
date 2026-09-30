from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FRONTEND=ROOT/"frontend"/"index.html"
def test_frontend_uses_session_bearer_token_and_request_deadline():
    text=FRONTEND.read_text(encoding="utf-8")
    assert "sessionStorage.getItem('disputeApiToken')" in text
    assert "new AbortController()" in text
    assert "setTimeout(()=>controller.abort(),30000)" in text
    assert "innerHTML='<strong>Masked input preview:" not in text
def test_frontend_does_not_send_client_authority_fields():
    text=FRONTEND.read_text(encoding="utf-8")
    assert "actor_id:'analyst:A-001'" not in text
    assert "reviewer_id:'reviewer:R-001'" not in text
    assert "role:'analyst'" not in text
