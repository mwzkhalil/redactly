"""Runtime configuration.

Every tunable that affects the security boundary (grant lifetimes, verification
rate limits, page sizes) is centralised here so the invariant tests can shrink
timeouts without touching service code.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]

MASTER_KEY_BYTES = 32


def _env(name: str, default: str) -> str:
    return os.environ.get(f"REDACTLY_{name}", default)


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _load_master_key() -> bytes:
    encoded = os.environ.get("REDACTLY_MASTER_KEY")
    if encoded:
        key = base64.urlsafe_b64decode(encoded)
        if len(key) != MASTER_KEY_BYTES:
            raise ValueError(f"REDACTLY_MASTER_KEY must decode to {MASTER_KEY_BYTES} bytes")
        return key

    # Development convenience only. A generated key is persisted outside the
    # repository tree so restarting the service does not orphan stored ciphertext.
    key_path = Path(_env("KEY_FILE", str(SERVICE_ROOT / "var" / "dev-master.key")))
    if key_path.is_file():
        return base64.urlsafe_b64decode(key_path.read_text(encoding="utf-8").strip())

    key = secrets.token_bytes(MASTER_KEY_BYTES)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="utf-8")
    return key


@dataclass(frozen=True)
class Settings:
    database_path: Path
    master_key: bytes
    detector: str
    model_id: str
    model_cache_dir: Path
    fixtures_dir: Path

    session_ttl_seconds: float
    approval_timeout_seconds: float
    grant_ttl_seconds: float

    verify_window_seconds: float
    verify_max_per_field: int
    verify_max_per_session: int

    view_page_characters: int
    audit_default_limit: int
    audit_max_limit: int

    cookie_name: str
    cookie_secure: bool
    allowed_origins: tuple[str, ...]

    @property
    def detector_is_onnx(self) -> bool:
        return self.detector == "onnx"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    database_path = Path(_env("DATABASE_PATH", str(SERVICE_ROOT / "var" / "redactly.sqlite3")))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    origins = tuple(
        origin.strip()
        for origin in _env("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    )
    return Settings(
        database_path=database_path,
        master_key=_load_master_key(),
        detector=_env("DETECTOR", "onnx"),
        model_id=_env("MODEL_ID", "gravitee-io/bert-small-pii-detection"),
        model_cache_dir=Path(_env("MODEL_CACHE_DIR", str(REPO_ROOT / "model-spike" / ".model-cache"))),
        fixtures_dir=Path(_env("FIXTURES_DIR", str(REPO_ROOT / "fixtures" / "synthetic"))),
        session_ttl_seconds=_env_float("SESSION_TTL_SECONDS", 60 * 60 * 8),
        approval_timeout_seconds=_env_float("APPROVAL_TIMEOUT_SECONDS", 120),
        grant_ttl_seconds=_env_float("GRANT_TTL_SECONDS", 60),
        verify_window_seconds=_env_float("VERIFY_WINDOW_SECONDS", 60),
        verify_max_per_field=_env_int("VERIFY_MAX_PER_FIELD", 5),
        verify_max_per_session=_env_int("VERIFY_MAX_PER_SESSION", 20),
        view_page_characters=_env_int("VIEW_PAGE_CHARACTERS", 900),
        audit_default_limit=_env_int("AUDIT_DEFAULT_LIMIT", 20),
        audit_max_limit=_env_int("AUDIT_MAX_LIMIT", 50),
        cookie_name=_env("COOKIE_NAME", "redactly_session"),
        cookie_secure=_env("COOKIE_SECURE", "0") == "1",
        allowed_origins=origins,
    )
