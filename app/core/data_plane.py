from __future__ import annotations
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app.models import Transaction, CustomerProfile, PriorDispute, BankAccount, CardSummary, Statement, Principal

class DataPlane:
    """Synthetic banking data plane. No real banking integrations are used in this cut."""
    def __init__(self, root="data/synthetic"):
        project_root = Path(__file__).resolve().parents[2]
        root_path = Path(root)
        root = root_path if root_path.is_absolute() else project_root / root_path
        self.txns = {}
        self.profiles = {}
        self.disputes = []
        self.accounts = {}
        self.cards = {}
        self.statements = {}
        portfolio_path = root / "portfolios.json"
        self.portfolios = json.loads(portfolio_path.read_text()) if portfolio_path.exists() else {}
        for x in json.loads((root / "transactions.json").read_text()):
            tx = Transaction.model_validate(x); self.txns[tx.transaction_id] = tx
        for x in json.loads((root / "customers.json").read_text()):
            p = CustomerProfile.model_validate(x); self.profiles[p.customer_id] = p
        self.disputes = [PriorDispute.model_validate(x) for x in json.loads((root / "disputes.json").read_text())]
        for x in json.loads((root / "accounts.json").read_text()):
            a = BankAccount.model_validate(x); self.accounts[a.customer_id] = a
        for x in json.loads((root / "cards.json").read_text()):
            c = CardSummary.model_validate(x); self.cards[c.customer_id] = c
        for x in json.loads((root / "statements.json").read_text()):
            s = Statement.model_validate(x); self.statements.setdefault(s.account_id, []).append(s)

    @staticmethod
    def _check(ctx, perm):
        if perm not in ctx.permissions:
            raise PermissionError("ACCESS_CONTEXT_INVALID")

    def get_transaction(self, ctx, transaction_id):
        self._check(ctx, "txn:read")
        t = self.txns.get(transaction_id)
        if not t or t.customer_id != ctx.customer_id: raise LookupError("TXN_NOT_FOUND")
        return t

    def get_recent_transactions(self, ctx, days=90, limit=20, as_of=None):
        self._check(ctx, "history:read")
        xs = [t for t in self.txns.values() if t.customer_id == ctx.customer_id]
        if xs:
            latest = (datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else max(datetime.fromisoformat(t.timestamp.replace("Z", "+00:00")) for t in xs))
            cutoff = latest - timedelta(days=max(days, 0))
            xs = [t for t in xs if cutoff <= datetime.fromisoformat(t.timestamp.replace("Z", "+00:00")) <= latest]
        return sorted(xs, key=lambda x: x.timestamp, reverse=True)[:limit]

    def get_prior_disputes(self, ctx, limit=10):
        self._check(ctx, "history:read")
        return [d for d in self.disputes if d.customer_id == ctx.customer_id][:limit]

    def get_customer_profile(self, ctx):
        self._check(ctx, "profile:read")
        return self.profiles[ctx.customer_id]

    def list_customers(self):
        return [self.profiles[k].model_dump(mode="json") for k in sorted(self.profiles)]

    def statement(self, customer_id: str, statement_id: str):
        account = self.accounts[customer_id]
        for statement in self.statements.get(account.account_id, []):
            if statement.statement_id == statement_id:
                return statement
        raise KeyError(statement_id)

    def statement_detail(self, customer_id: str, statement_id: str):
        statement = self.statement(customer_id, statement_id)
        txns = []
        for tx in self.txns.values():
            if tx.customer_id != customer_id:
                continue
            date = tx.timestamp[:10]
            if statement.period_start <= date <= statement.period_end:
                txns.append(tx.model_dump(mode="json"))
        txns.sort(key=lambda x: x["timestamp"], reverse=False)
        return {"statement": statement.model_dump(mode="json"), "lines": txns}

    def dashboard(self, customer_id):
        account = self.accounts[customer_id]
        card = self.cards[customer_id]
        txns = sorted((t for t in self.txns.values() if t.customer_id == customer_id), key=lambda x: x.timestamp, reverse=True)
        statements = sorted(self.statements.get(account.account_id, []), key=lambda x: x.statement_date, reverse=True)
        debits = sum((t.amount for t in txns), Decimal("0.00"))
        dispute_items = [x for x in self.disputes if x.customer_id == customer_id]
        category_totals: dict[str, Decimal] = {}
        for t in txns:
            category_totals[t.category] = category_totals.get(t.category, Decimal("0.00")) + t.amount
        return {
            "profile": self.profiles[customer_id].model_dump(mode="json"),
            "account": account.model_dump(mode="json"),
            "card": card.model_dump(mode="json"),
            "statements": [x.model_dump(mode="json") for x in statements],
            "transactions": [x.model_dump(mode="json") for x in txns],
            "disputes": [x.model_dump(mode="json") for x in dispute_items],
            "relationship": {
                "monthly_debit_proxy": debits,
                "transaction_count": len(txns),
                "category_totals": {k: str(v) for k, v in sorted(category_totals.items())},
                "active_disputes": sum(1 for x in dispute_items if x.outcome not in {"accepted", "rejected"}),
                "statement_count": len(statements),
            },
        }
