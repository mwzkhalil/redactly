"""Key derivation, document encryption, and equality digests.

One master key is expanded with HKDF into purpose-specific subkeys. Nothing uses
the master key directly, so a digest computed for one purpose can never be
replayed against another.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings

NONCE_BYTES = 12
PUBLIC_REF_BYTES = 12

_DOCUMENT_INFO = b"redactly/document-encryption/v1"
_SESSION_INFO = b"redactly/session-token/v1"
_CANONICAL_INFO = b"redactly/canonical-equality/v1"


def derive_key(info: bytes, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,
    ).derive(get_settings().master_key)


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def public_ref(prefix: str) -> str:
    """Random, unguessable, and meaningless outside its own session row."""
    return f"{prefix}_{secrets.token_urlsafe(PUBLIC_REF_BYTES)}"


def encrypt_document(slug: str, text: str) -> tuple[bytes, bytes]:
    key = derive_key(_DOCUMENT_INFO)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, text.encode("utf-8"), _document_aad(slug))
    return ciphertext, nonce


def decrypt_document(slug: str, ciphertext: bytes, nonce: bytes) -> str:
    key = derive_key(_DOCUMENT_INFO)
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, _document_aad(slug))
    return plaintext.decode("utf-8")


def _document_aad(slug: str) -> bytes:
    # Binding the slug means ciphertext cannot be moved between document rows.
    return f"redactly/document/{slug}".encode("utf-8")


def session_token() -> tuple[str, str]:
    """Return `(token, token_hmac)`. Only the digest is ever persisted."""
    token = secrets.token_urlsafe(32)
    return token, session_token_digest(token)


def session_token_digest(token: str) -> str:
    key = derive_key(_SESSION_INFO)
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def canonical_digest(canonical_value: str, domain_separator: str) -> bytes:
    """Keyed digest of a canonicalised field value.

    The separator is unique per field, so two documents containing the same email
    address produce unrelated digests. Without that, the digest column itself
    would be a cross-document correlation oracle even though it is never returned.
    """
    key = derive_key(_CANONICAL_INFO + b"|" + domain_separator.encode("utf-8"))
    return hmac.new(key, canonical_value.encode("utf-8"), hashlib.sha256).digest()


def field_domain_separator(slug: str, content_sha256: str, start_offset: int, end_offset: int) -> str:
    """Uniquely name one field, including for two documents with identical text."""
    return f"{slug}:{content_sha256}:{start_offset}:{end_offset}"


def digests_equal(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)
