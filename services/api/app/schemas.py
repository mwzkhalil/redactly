"""Request bodies.

The browser-side JSON Schema in a WebMCP tool descriptor is a hint to the agent,
not a security boundary — nothing stops a caller from posting to this API
directly. Every argument is therefore validated again here, with `extra="forbid"`
so an unexpected key is a hard error rather than something a future handler might
start reading.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

FIELD_REF_PATTERN = r"^fld_[A-Za-z0-9_-]{8,64}$"
REQUEST_REF_PATTERN = r"^req_[A-Za-z0-9_-]{8,64}$"
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ViewRequest(StrictModel):
    purpose: str = Field(min_length=1, max_length=280)
    cursor: str | None = Field(default=None, max_length=512)


class VerifyRequest(StrictModel):
    field_ref: str = Field(pattern=FIELD_REF_PATTERN)
    comparison_value: str = Field(min_length=1, max_length=256)
    purpose: str = Field(min_length=1, max_length=280)


class UnmaskRequest(StrictModel):
    field_ref: str = Field(pattern=FIELD_REF_PATTERN)
    reason: str = Field(min_length=1, max_length=280)


class ChallengeRequest(StrictModel):
    field_ref: str = Field(pattern=FIELD_REF_PATTERN)
    reasoning: str = Field(min_length=1, max_length=280)


class AuditRequest(StrictModel):
    purpose: str = Field(min_length=1, max_length=280)
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=20, ge=1, le=50)


class DecisionRequest(StrictModel):
    approve: bool
    note: str | None = Field(default=None, max_length=280)
