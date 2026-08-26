"""Download and load the ONNX detector, for baking it into a container image.

Run at build time. Downloading is only half the point: constructing the detector
also builds the ONNX session, so a truncated or incompatible artefact fails the
build instead of failing the first visitor.

    uv run python scripts/prefetch_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.inference.base import DetectorUnavailable, get_detector  # noqa: E402

PROBE = "Contact Avery Morgan at avery.morgan@example.com or +1 (202) 555-0147."


def main() -> int:
    settings = get_settings()
    if not settings.detector_is_onnx:
        print(f"detector is {settings.detector!r}; nothing to prefetch")
        return 0

    try:
        detector = get_detector()
    except DetectorUnavailable as error:
        print(f"detector unavailable: {error}", file=sys.stderr)
        return 1

    # A model that loads but predicts nothing would still pass a download check.
    spans = detector.detect(PROBE)
    print(f"model      {detector.name}")
    print(f"revision   {detector.revision}")
    print(f"onnx sha   {detector.onnx_sha256}")
    print(f"cache dir  {settings.model_cache_dir}")
    print(f"probe      {len(spans)} span(s): {sorted({span.category for span in spans})}")

    if not spans:
        print("the probe sentence produced no spans; the artefact is suspect", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
