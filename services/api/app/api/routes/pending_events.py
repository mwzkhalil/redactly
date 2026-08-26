"""Long-poll watcher for the owner queue.

An agent's unmask tool call blocks while it waits for a human, so the approval UI
has to appear within a second or two of the request or the tool times out. Long
polling on a cheap signature keeps that latency low without a websocket, and
returns immediately when the caller navigates away.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query, Request

from ...db import session as db
from ...services import approvals
from ..deps import SessionContext, current_session

router = APIRouter(prefix="/owner", tags=["owner"])

POLL_INTERVAL_SECONDS = 0.3
MAX_WAIT_SECONDS = 25.0


@router.get("/pending/watch")
async def watch_pending(
    http_request: Request,
    signature: str | None = Query(default=None, max_length=64),
    timeout: float = Query(default=MAX_WAIT_SECONDS, gt=0, le=MAX_WAIT_SECONDS),
    session: SessionContext = Depends(current_session),
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while True:
        current = await db.run(approvals.pending_signature, session_id=session.id)
        if current != signature:
            requests = await db.run(approvals.list_pending, session_id=session.id)
            return {"changed": True, "signature": current, "requests": requests}
        if time.monotonic() >= deadline or await http_request.is_disconnected():
            return {"changed": False, "signature": current, "requests": []}
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
