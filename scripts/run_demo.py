from __future__ import annotations

import json
from pathlib import Path

from app.workflow import Copilot

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cop = Copilot()
    cid = cop.db.create_case("C-1001", "analyst:A-001", "T-1007")
    out = cop.run(cid, "analyst:A-001", "I do not recognize this transaction and want to dispute it.")
    payload = out.model_dump(mode="json")
    target = ROOT / "runtime" / "demo_result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nDemo result: {target}")


if __name__ == "__main__":
    main()
