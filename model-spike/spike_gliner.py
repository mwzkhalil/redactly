"""Load NVIDIA GLiNER-PII, run inference, export ONNX, and verify ONNX inference.

This is deliberately a spike, not production service code. All sample values are
synthetic. Model weights and generated artifacts stay in ignored local folders.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import onnx
import onnxruntime as ort
from gliner import GLiNER
from huggingface_hub import HfApi


DEFAULT_MODEL = "nvidia/gliner-PII"
LABELS = [
    "person name",
    "social security number",
    "identity number",
    "bank account number",
    "phone number",
    "email address",
    "street address",
    "date of birth",
    "monetary amount",
]


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def predict(model: GLiNER, text: str, threshold: float) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    entities = model.predict_entities(text, LABELS, threshold=threshold)
    elapsed_ms = (time.perf_counter() - started) * 1_000
    return entities, elapsed_ms


def export_model(model: GLiNER, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    export = model.export_to_onnx
    parameters = inspect.signature(export).parameters
    kwargs: dict[str, Any] = {}

    if "quantize" in parameters:
        kwargs["quantize"] = False
    if "save_directory" in parameters:
        kwargs["save_directory"] = str(output_dir)
        export(**kwargs)
    elif "save_dir" in parameters:
        kwargs["save_dir"] = str(output_dir)
        export(**kwargs)
    else:
        export(str(output_dir), **kwargs)

    return sorted(output_dir.rglob("*.onnx"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sample", type=Path, default=Path(__file__).with_name("sample_claim.txt"))
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).parent / ".model-cache")
    parser.add_argument("--artifacts-dir", type=Path, default=Path(__file__).parent / "artifacts" / "nvidia-gliner-pii")
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(args.cache_dir.resolve()))
    text = args.sample.read_text(encoding="utf-8")

    info = HfApi().model_info(args.model, files_metadata=True)
    emit(
        "model_resolved",
        model=args.model,
        revision=info.sha,
        license=(info.card_data or {}).get("license"),
        siblings={item.rfilename: item.size for item in info.siblings},
        python=sys.version.split()[0],
        platform=platform.platform(),
    )

    started = time.perf_counter()
    model = GLiNER.from_pretrained(
        args.model,
        map_location="cpu",
        cache_dir=str(args.cache_dir.resolve()),
    )
    emit("pytorch_loaded", elapsed_seconds=round(time.perf_counter() - started, 3))

    entities, latency_ms = predict(model, text, args.threshold)
    emit("pytorch_inference", latency_ms=round(latency_ms, 2), entities=entities)

    result: dict[str, Any] = {
        "model": args.model,
        "revision": info.sha,
        "license": (info.card_data or {}).get("license"),
        "labels": LABELS,
        "threshold": args.threshold,
        "pytorch_latency_ms": round(latency_ms, 2),
        "pytorch_entities": entities,
        "onnx": None,
    }

    if not args.skip_export:
        started = time.perf_counter()
        onnx_paths = export_model(model, args.artifacts_dir)
        emit(
            "onnx_exported",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            files=[{"path": str(path), "bytes": path.stat().st_size} for path in onnx_paths],
        )
        if not onnx_paths:
            raise RuntimeError("GLiNER export completed without producing an ONNX file")

        onnx_path = next((path for path in onnx_paths if "quant" not in path.name.lower()), onnx_paths[0])
        onnx.checker.check_model(onnx.load(str(onnx_path), load_external_data=True))
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        emit(
            "onnx_graph_valid",
            path=str(onnx_path),
            inputs=[{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_inputs()],
            outputs=[{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_outputs()],
        )

        # GLiNER's loader needs the exported config/tokenizer that export_to_onnx writes.
        onnx_model = GLiNER.from_pretrained(
            str(args.artifacts_dir),
            load_onnx_model=True,
            load_tokenizer=True,
            onnx_model_file=onnx_path.name,
            map_location="cpu",
        )
        onnx_entities, onnx_latency_ms = predict(onnx_model, text, args.threshold)
        emit("onnx_inference", latency_ms=round(onnx_latency_ms, 2), entities=onnx_entities)
        result["onnx"] = {
            "path": str(onnx_path),
            "bytes": onnx_path.stat().st_size,
            "latency_ms": round(onnx_latency_ms, 2),
            "entities": onnx_entities,
        }

    report_path = args.artifacts_dir / "report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", report=str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

