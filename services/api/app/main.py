"""FastAPI application.

Note what is *not* mounted: there is no route that runs the detector on
caller-supplied text and returns spans. Such a route would be an oracle for
probing the classifier, and if it were ever registered as a tool it would hand an
agent a way to submit a document and read back its own PII map.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import agent_tools, documents, owner_decisions, pending_events, sessions
from .config import get_settings
from .db import session as db
from .domain.enums import ToolStatus
from .security.log_filters import configure_logging
from .services import redaction

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    db.initialise()
    if settings.fixtures_dir.is_dir():
        slugs = await db.run(redaction.seed_from_fixtures, settings.fixtures_dir)
        logger.info("fixtures seeded", extra={"event": "fixtures_seeded", "count": len(slugs)})
    yield


app = FastAPI(
    title="Redactly policy service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)

app.include_router(sessions.router)
app.include_router(documents.router)
app.include_router(agent_tools.router)
app.include_router(owner_decisions.router)
app.include_router(pending_events.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    """Reject bad arguments without echoing them.

    Pydantic's default body includes the offending input. For `comparison_value`
    that would write an agent's guess into an error response and any log that
    captures it.
    """
    fields = sorted({".".join(str(part) for part in item["loc"][1:]) for item in error.errors()})
    return JSONResponse(
        status_code=422,
        content={"status": "invalid_arguments", "fields": fields[:10]},
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
    logger.exception("unhandled error", extra={"event": "unhandled_error"})
    return JSONResponse(
        status_code=500,
        content={"status": str(ToolStatus.UNAVAILABLE), "reason": "internal_error"},
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "detector": settings.detector}
