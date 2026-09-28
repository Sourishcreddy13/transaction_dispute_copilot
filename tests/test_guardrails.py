from src.guardrails.validators import validate_customer_view, sanitize_output

def test_customer_view_blocks_internal_details():
    text = sanitize_output('Your score is 0.91; rule R-123; /internal/path')
    try:
        validate_customer_view(text)
    except ValueError:
        pass
    else:
        raise AssertionError('customer view should reject internal fraud/policy details')

def test_customer_view_safe():
    text = sanitize_output('Your dispute is under specialist review.')
    validate_customer_view(text)
