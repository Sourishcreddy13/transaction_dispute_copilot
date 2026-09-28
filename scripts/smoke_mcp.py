from __future__ import annotations
import asyncio, os, json
from pathlib import Path
from app.core.security import mint_context
from app.models import Principal, Role
from src.mcp_client import BankingMCPClient

async def main():
    principal=Principal(actor_id='analyst:A-001', role=Role.analyst)
    token=mint_context(principal,'CASE-MCP-SMOKE','C-1001',['txn:read','history:read','profile:read','account:read'],os.environ['ACCESS_SECRET'])
    client=await BankingMCPClient().initialize()
    await client.call('get_transaction',{'access_context':token,'transaction_id':'T-1007'})
    await client.call('get_recent_transactions',{'access_context':token,'window_days':90,'limit':5})
    await client.call('get_customer_profile',{'access_context':token})
    await client.call('get_account_summary',{'access_context':token})
    await client.call('get_statements',{'access_context':token,'limit':3})
    resource=await client.resource('policy://chargeback/manual')
    Path('logs/mcp_transcript.jsonl').parent.mkdir(exist_ok=True)
    Path('logs/mcp_resource_smoke.json').write_text(json.dumps({'resource':'policy://chargeback/manual','length':len(resource)}))

if __name__=='__main__': asyncio.run(main())
