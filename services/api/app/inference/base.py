"""Detector interface and factory."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

from ..config import get_settings


class DetectorUnavailable(RuntimeError):
    """Raised when detection cannot be performed.

    Callers treat this as fail-closed: the document is marked FAILED and no
    partially-masked view is ever produced from it.
    """


@dataclass(frozen=True, slots=True)
class DetectedSpan:
    start: int
    end: int
    base_entity_type: str
    category: str
    confidence: float


@runtime_checkable
class Detector(Protocol):
    name: str
    revision: str | None
    onnx_sha256: str | None

    def detect(self, text: str) -> list[DetectedSpan]: ...


@lru_cache(maxsize=1)
def get_detector() -> Detector:
    settings = get_settings()
    if settings.detector_is_onnx:
        from .onnx_detector import OnnxTokenClassifier

        return OnnxTokenClassifier()

    from .deterministic import DeterministicDetector

    return DeterministicDetector()
