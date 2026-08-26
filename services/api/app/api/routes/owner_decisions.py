"""The human side of the approval loop.

This is the weakest link in the demo and it is worth being explicit about why: the
modal renders in the same page an agent can drive, so a browser-resident agent
could in principle observe the queue or race the click. Production would move
this to an authenticated out-of-band channel. What the demo does guarantee is
that approval is a *separate actor's* action recorded as `HUMAN` in the audit log
and that approving twice still only releases one value.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from ...db import session as db
from ...schemas import REQUEST_REF_PATTERN, DecisionRequest
from ...services import approvals
from ..deps import SessionContext, current_session

router = APIRouter(prefix="/owner", tags=["owner"])


@router.get("/pending")
async def pending_requests(session: SessionContext = Depends(current_session)) -> dict[str, object]:
    requests = await db.run(approvals.list_pending, session_id=session.id)
    signature = await db.run(approvals.pending_signature, session_id=session.id)
    return {"requests": requests, "signature": signature}


@router.post("/requests/{request_ref}/decision")
async def decide(
    body: DecisionRequest,
    request_ref: str = Path(pattern=REQUEST_REF_PATTERN),
    session: SessionContext = Depends(current_session),
) -> dict[str, object]:
    return await db.run(
        approvals.resolve_decision,
        session_id=session.id,
        request_ref=request_ref,
        approve=body.approve,
        note=body.note,
    )
