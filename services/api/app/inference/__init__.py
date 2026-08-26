"""PII detection. Never exposed as a WebMCP tool."""

from .base import DetectedSpan, Detector, DetectorUnavailable, get_detector

__all__ = ["DetectedSpan", "Detector", "DetectorUnavailable", "get_detector"]
