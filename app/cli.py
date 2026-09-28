from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.models import Role
from app.workflow import Copilot


def main() -> None:
    parser = argparse.ArgumentParser(prog="copilot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    open_case = sub.add_parser("open-case")
    open_case.add_argument("--customer", required=True)
    open_case.add_argument("--transaction")
    open_case.add_argument("--as", dest="actor", default="analyst:A-001")

    run = sub.add_parser("run")
    run.add_argument("--case", required=True)
    run.add_argument("--input", required=True)
    run.add_argument("--as", dest="actor", default="analyst:A-001")

    review = sub.add_parser("review")
    review.add_argument("--task", required=True)
    review.add_argument("--action", choices=["provisional_credit", "chargeback", "investigate", "deny"])
    review.add_argument("--version", type=int, required=True)
    review.add_argument("--as", dest="actor", default="reviewer:R-001")
    review.add_argument("--reason-code", default="EVIDENCE_SUFFICIENT")
    review.add_argument("--note", default="")

    sub.add_parser("outbox")
    sub.add_parser("evidence")
    sub.add_parser("reconcile")

    args = parser.parse_args()
    cop = Copilot()

    if args.cmd == "open-case":
        principal = cop.s.principal(args.actor, Role.analyst)
        cop.s.authorize_case_creation(principal, args.customer)
        case_id = cop.db.create_case(args.customer, args.actor, args.transaction)
        print(json.dumps({"case_id": case_id}, indent=2))
        return

    if args.cmd == "run":
        source = Path(args.input)
        text = source.read_text(encoding="utf-8") if source.exists() else args.input
        print(cop.run(args.case, args.actor, text).model_dump_json(indent=2))
        return

    if args.cmd == "review":
        row = cop.db.get_review(args.task)
        if not row:
            raise SystemExit("REVIEW_NOT_FOUND")
        if not cop.db.claim_review(args.task, args.actor, args.version - 1):
            # The supplied version may already represent a claimed task.
            pass
        if args.action:
            resolved = cop.db.resolve_review(
                args.task,
                args.actor,
                args.version,
                row["case_id"],
                args.action,
                args.reason_code,
                args.note,
            )
            if not resolved:
                raise SystemExit("REVIEW_CONFLICT")
        print(json.dumps(dict(cop.db.get_review(args.task)), indent=2, default=str))
        return

    if args.cmd == "outbox":
        from app.core.outbox import OutboxWorker

        print(json.dumps({"delivered": OutboxWorker(cop.db).deliver_once()}, indent=2))
        return

    if args.cmd == "evidence":
        import subprocess
        import sys

        subprocess.run([sys.executable, "scripts/generate_evidence.py"], check=True)
        return

    if args.cmd == "reconcile":
        # Local reference implementation: inspect unresolved review tasks.
        print(json.dumps({"pending_reviews": [dict(r) for r in cop.db.cases_for_queue()]}, indent=2, default=str))


if __name__ == "__main__":
    main()
