import sys

from app.cli import main


class _Row(dict):
    pass


class _FakeDB:
    def __init__(self):
        self.row = _Row(
            task_id="TASK-1", case_id="CASE-1", version=0, status="PENDING",
            claimed_by=None, recommendation_creator="analyst:A-001", expires_at=None, claim_expires_at=None
        )

    def get_review(self, _task):
        return self.row.copy()

    def claim_review(self, _task, reviewer, expected_version):
        assert expected_version == 0
        self.row.update(status="CLAIMED", claimed_by=reviewer, version=1)
        return True

    def resolve_review(self, task, reviewer, expected_version, case_id, action, reason, note):
        assert task == "TASK-1"
        assert reviewer == "reviewer:R-001"
        assert expected_version == 1
        assert case_id == "CASE-1"
        assert action == "investigate"
        self.row.update(status="RESOLVED", version=2)
        return True


class _FakeCopilot:
    def __init__(self):
        self.db = _FakeDB()
        self.s = object()

    async def _resume(self):
        return {"resumed": True}

    def resume_review(self, task, actor):
        assert task == "TASK-1"
        assert actor == "reviewer:R-001"
        return self._resume()


def test_cli_review_claims_pending_then_resolves(monkeypatch):
    fake = _FakeCopilot()
    monkeypatch.setattr("app.cli.Copilot", lambda: fake)
    monkeypatch.setattr(
        sys, "argv", [
            "copilot", "review", "--task", "TASK-1", "--version", "0",
            "--action", "investigate", "--as", "reviewer:R-001",
        ]
    )
    main()
    assert fake.db.row["status"] == "RESOLVED"
