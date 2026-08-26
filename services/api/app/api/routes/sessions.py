"""Session bootstrap.

There is no login. A session cookie isolates one browser's view of a document
from another's, which is enough to keep demo traffic from cross-contaminating,
and explicitly not enough to establish document ownership.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ...config import get_settings
from ...db import session as db
from ...security import sessions

router = APIRouter(prefix="/session", tags=["session"])


@router.post("")
async def start_session(response: Response) -> dict[str, object]:
    settings = get_settings()
    token, session_id = await db.run(sessions.create_session)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=int(settings.session_ttl_seconds),
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )
    # The token is never echoed in the body: a body value would be readable by
    # any script on the page, which defeats the point of an HttpOnly cookie.
    return {"status": "ok", "session_started": True}


@router.get("")
async def session_status(request: Request) -> dict[str, object]:
    token = request.cookies.get(get_settings().cookie_name)
    row = await db.run(sessions.resolve_session, token)
    return {"active": row is not None}
