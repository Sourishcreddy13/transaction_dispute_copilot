from __future__ import annotations
import json, sys
from app.core.db import DB

case=sys.argv[1] if len(sys.argv)>1 else None
if not case: raise SystemExit('usage: uv run python scripts/replay_decision.py CASE-...')
db=DB(); rows=db.get_audit(case)
for r in rows:
    if r['stage']=='recommendation':
        payload=json.loads(r['payload_json'])
        snap=db.get_snapshot(payload['snapshot_id'])
        print(json.dumps({'case_id':case,'snapshot_id':payload['snapshot_id'],'recommendation':payload['recommendation'],'snapshot_hash':snap['content_hash'] if snap else None},indent=2))
        break
else: raise SystemExit('No recommendation audit event found')
