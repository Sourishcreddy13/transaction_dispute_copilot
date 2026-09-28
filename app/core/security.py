import base64, hashlib, hmac, json, time, uuid
from app.models import AccessContext, Principal

def _enc(x): return base64.urlsafe_b64encode(x).decode().rstrip('=')
def _dec(x): return base64.urlsafe_b64decode(x+'='*((4-len(x)%4)%4))

def mint_context(principal,case_id,customer_id,permissions,secret,ttl=60,audience="mcp"):
    ctx=AccessContext(context_id='CTX-'+uuid.uuid4().hex,case_id=case_id,customer_id=customer_id,actor_id=principal.actor_id,role=principal.role,
                      permissions=permissions,expires_at=time.time()+ttl,audience=audience,nonce=uuid.uuid4().hex)
    raw=ctx.model_dump_json().encode(); return _enc(raw)+'.'+_enc(hmac.new(secret.encode(),raw,hashlib.sha256).digest())

def verify_context(token,secret,required,audience="mcp"):
    try:
        a,b=token.split('.'); raw=_dec(a); sig=_dec(b); expected=hmac.new(secret.encode(),raw,hashlib.sha256).digest()
        if not hmac.compare_digest(sig,expected): raise ValueError
        ctx=AccessContext.model_validate_json(raw)
        if ctx.expires_at < time.time() or required not in ctx.permissions or ctx.audience != audience: raise ValueError
        return ctx
    except Exception as e: raise PermissionError('ACCESS_CONTEXT_INVALID') from e
