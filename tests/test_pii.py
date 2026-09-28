def test_pii_redaction():
 from app.core.pii import PIIService
 r=PIIService('regex').scan('email test@example.com card 4111111111111111')
 assert 'test@example.com' not in r.masked_text and '4111111111111111' not in r.masked_text

def test_plain_account_number_is_masked_in_output():
    from src.guardrails.validators import sanitize_output
    masked = sanitize_output("Account 1234567890123456")
    assert "1234567890123456" not in masked
