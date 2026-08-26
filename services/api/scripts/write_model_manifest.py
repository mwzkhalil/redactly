"""Record exactly which model artefact produced a redaction.

A redaction is a claim about a document, and the claim is only reproducible if the
graph that made it is pinned. This writes the model id, resolved revision,
license, and the SHA-256 of the ONNX file actually loaded.

    uv run python scripts/write_model_manifest.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT, get_settings  # noqa: E402
from app.inference import DetectorUnavailable  # noqa: E402
from app.inference.onnx_detector import OnnxTokenClassifier  # noqa: E402


def main() -> int:
    settings = get_settings()
    output = REPO_ROOT / "models" / "model-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        detector = OnnxTokenClassifier()
    except DetectorUnavailable as error:
        print(f"detector unavailable: {error}", file=sys.stderr)
        return 1

    license_name = None
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(settings.model_id)
        license_name = (info.card_data or {}).get("license")
        revision = info.sha
    except Exception:  # noqa: BLE001 - offline is fine, the SHA is the real anchor
        revision = detector.revision

    manifest = {
        "model_id": detector.name,
        "revision": revision,
        "license": license_name,
        "onnx_sha256": detector.onnx_sha256,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
