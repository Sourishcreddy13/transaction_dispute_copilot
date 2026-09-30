import pytest
import inspect
from mcp_server import server

def test_mcp_tools_and_resource_exist():
    assert callable(server.get_transaction)
    assert callable(server.get_recent_transactions)
    assert callable(server.get_customer_profile)
    assert callable(server.get_prior_disputes)
    assert callable(server.get_account_summary)
    assert callable(server.get_statements)
    assert callable(server.chargeback_policy_resource)

def test_tool_contracts_do_not_accept_customer_id_directly():
    sig=inspect.signature(server.get_transaction)
    assert "customer_id" not in sig.parameters
    assert "access_context" in sig.parameters


def test_unknown_transaction_returns_non_enumerating_error(monkeypatch):
    from app.core.security import mint_context
    from app.models import Principal, Role
    # The token must be signed with whatever secret the MCP server will
    # actually verify against (ACCESS_SECRET, default "dev-only-change-me"),
    # not an arbitrary literal the test happens to pick.
    monkeypatch.setenv("ACCESS_SECRET", "test-secret-0123456789-abcdef-0123456789")
    principal = Principal(actor_id="analyst:A-001", role=Role.analyst)
    token = mint_context(principal, "CASE-1", "C-1001", ["txn:read"], "test-secret-0123456789-abcdef-0123456789")
    try:
        server.get_transaction(token, "T-NOT-FOUND")
    except LookupError as exc:
        assert str(exc) == "TXN_NOT_FOUND"
    else:
        raise AssertionError("unknown transaction must use the non-enumerating error")


def test_all_mcp_tools_have_access_context_contract():
    import inspect
    from mcp_server import server

    names = [
        "get_transaction",
        "get_recent_transactions",
        "get_customer_profile",
        "get_prior_disputes",
        "get_account_summary",
        "get_statements",
    ]
    for name in names:
        sig = inspect.signature(getattr(server, name))
        assert "access_context" in sig.parameters

def test_unknown_mcp_result_content_fails_closed():
    from src.mcp_client import parse_mcp_content
    with pytest.raises(ValueError, match="MCP_RESPONSE_CONTRACT_INVALID"):
        parse_mcp_content([{"type":"image","data":"abc"}])
