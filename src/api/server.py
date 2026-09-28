"""Standalone HTTP entry point for FIN-C2-025 (released AgentCore SDK v1.0.0).

Adapter only — no business logic. AgentGateway normally calls agent.invoke() directly;
this server is for local/standalone invocation. The agent inherits AgentBaseGraph, so
`invoke(user_input, session_id, ctx, input_context)` runs the fixed backbone
(initialize → pre_process[S-2] → main → post_process[S-3/S-4] → finalize).

Customer profile (risk tolerance etc.) is passed via input_context["customer_profile"]
(read-only) — never in the State payload (PII / 金商法 KYC, docs/02_design.md §4).

Entry authentication:
  - a verified middleware trust (request.state.trust_level) is used as-is;
  - otherwise INVOKE_AUTH_TOKEN Bearer auth promotes ANONYMOUS →
    VERIFIED_EXTERNAL (_resolve_trust raises 401 on token mismatch);
  - an unauthenticated caller (no verified middleware trust AND no token
    promotion) is rejected with 401. The old fallback that defaulted such
    callers to ANONYMOUS and passed them straight into invoke() was fail-open
    (the per-node S-1 gate then failed deep in the graph instead of at the door);
    it is removed.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.agent import InvestmentSuitabilityQAAgent
from src.entry_adapter.auth import _require_verified_trust, _resolve_trust

app = FastAPI(title="FIN-C2-025 InvestmentSuitabilityQAAgent")

agent = InvestmentSuitabilityQAAgent()


def _ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Scope note: the entry_adapter part also exposes a tenant/org scope contract,
    but this template has NO scope model. The suitability KB is a single shared
    corpus keyed only by query; there is no per-request tenant/org key that any
    code path reads. Enforcing authenticate_scope() would gate on a scope nothing
    consumes (STG-default theater), so only the auth core (_resolve_trust /
    _require_verified_trust) is wired.
    """
    trust, token_authenticated = _resolve_trust(request)
    # Unauthenticated = trust is still ANONYMOUS after resolution and no token
    # promotion. Test the trust VALUE, not attribute presence — a middleware
    # that explicitly stamps ANONYMOUS must still be treated as unauthenticated
    # (→ 401), not silently accepted as "verified because the attr exists".
    if trust is TrustLevel.ANONYMOUS and not token_authenticated:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    _require_verified_trust(trust)
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=trust,
        caller_id=getattr(request.state, "caller_id", ""),
    )


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    session_id = req.session_id or str(uuid4())
    ctx = _ctx(request, session_id)
    # 顧客プロファイル（PII）は read-only な input_context で渡す（State には入れない）。
    customer_profile = getattr(request.state, "customer_profile", None)
    input_context = {"customer_profile": customer_profile} if customer_profile else {}
    return agent.invoke(
        user_input=req.input,
        session_id=session_id,
        ctx=ctx,
        input_context=input_context,
    )


@app.get("/health")
def health():
    return {"status": "ok", "agent": "investment-suitability-qa"}
