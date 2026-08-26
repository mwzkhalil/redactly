"""Run a pre-converted Hugging Face token classifier with ONNX Runtime.

Defaults to the compact Apache-2.0 Gravitee PII detector. No PyTorch model is
loaded for inference. All sample values are synthetic.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort
from huggingface_hub import HfApi, snapshot_download
from transformers import AutoConfig, AutoTokenizer


DEFAULT_MODEL = "gravitee-io/bert-small-pii-detection"
CANONICAL_TYPES = {
    "PERSON": "PERSON",
    "HONORIFIC": "PERSON",
    "TITLE": "PERSON",
    "US_SSN": "SSN",
    "US_ITIN": "ID_NUMBER",
    "US_DRIVER_LICENSE": "ID_NUMBER",
    "US_PASSPORT": "ID_NUMBER",
    "US_BANK_NUMBER": "ACCOUNT_NUMBER",
    "PHONE_NUMBER": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "LOCATION": "ADDRESS",
    "DATE_TIME": "DATE_OF_BIRTH",
    "FINANCIAL": "MONETARY_AMOUNT",
}


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def split_bio(label: str) -> tuple[str, str]:
    if label == "O":
        return "O", "O"
    for separator in ("-", "_"):
        if len(label) > 2 and label[0] in {"B", "I"} and label[1] == separator:
            return label[0], label[2:]
    return "B", label


def aggregate_spans(
    text: str,
    offsets: np.ndarray,
    label_ids: np.ndarray,
    probabilities: np.ndarray,
    id2label: dict[int, str],
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal active
        if active is None:
            return
        active["text"] = text[active["start"] : active["end"]]
        active["confidence"] = round(float(min(active.pop("token_confidences"))), 6)
        active["canonical_type"] = CANONICAL_TYPES.get(active["entity_type"], active["entity_type"])
        spans.append(active)
        active = None

    for offset, label_id, token_probs in zip(offsets, label_ids, probabilities, strict=True):
        start, end = int(offset[0]), int(offset[1])
        if start == end:
            continue
        raw_label = id2label[int(label_id)]
        prefix, entity_type = split_bio(raw_label)
        confidence = float(token_probs[int(label_id)])
        if entity_type == "O":
            finish()
            continue

        continues = (
            active is not None
            and active["entity_type"] == entity_type
            and prefix == "I"
            and start <= active["end"] + 1
        )
        if not continues:
            finish()
            active = {
                "start": start,
                "end": end,
                "entity_type": entity_type,
                "token_confidences": [confidence],
            }
        else:
            active["end"] = end
            active["token_confidences"].append(confidence)

    finish()
    return spans


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--onnx-file", default="model.onnx")
    parser.add_argument("--sample", type=Path, default=Path(__file__).with_name("sample_claim.txt"))
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).parent / ".model-cache")
    parser.add_argument("--artifacts-dir", type=Path, default=Path(__file__).parent / "artifacts" / "onnx-token-classifier")
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
    model_dir = Path(
        snapshot_download(
            repo_id=args.model,
            revision=info.sha,
            cache_dir=str(args.cache_dir.resolve()),
            allow_patterns=["*.json", "*.txt", "*.model", "*.onnx", "onnx/*.onnx"],
        )
    )
    emit("model_downloaded", elapsed_seconds=round(time.perf_counter() - started, 3), path=str(model_dir))

    candidates = [model_dir / args.onnx_file, model_dir / "onnx" / args.onnx_file]
    onnx_path = next((path for path in candidates if path.is_file()), None)
    if onnx_path is None:
        available = [str(path.relative_to(model_dir)) for path in model_dir.rglob("*.onnx")]
        raise FileNotFoundError(f"{args.onnx_file!r} was not found; available ONNX files: {available}")

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, use_fast=True)
    config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
    id2label = {int(key): value for key, value in config.id2label.items()}

    onnx.checker.check_model(onnx.load(str(onnx_path), load_external_data=True))
    started = time.perf_counter()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    load_ms = (time.perf_counter() - started) * 1_000
    input_names = {item.name for item in session.get_inputs()}

    encoded = tokenizer(
        text,
        return_tensors="np",
        return_offsets_mapping=True,
        truncation=True,
        max_length=min(getattr(config, "max_position_embeddings", 512), 512),
    )
    offsets = encoded.pop("offset_mapping")[0]
    feeds = {
        name: np.asarray(encoded[name], dtype=np.int64)
        for name in input_names
        if name in encoded
    }

    started = time.perf_counter()
    logits = session.run(None, feeds)[0][0]
    inference_ms = (time.perf_counter() - started) * 1_000
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(shifted) / np.exp(shifted).sum(axis=-1, keepdims=True)
    label_ids = logits.argmax(axis=-1)
    spans = aggregate_spans(text, offsets, label_ids, probabilities, id2label)

    report = {
        "model": args.model,
        "revision": info.sha,
        "license": (info.card_data or {}).get("license"),
        "onnx_path": str(onnx_path),
        "onnx_bytes": onnx_path.stat().st_size,
        "providers": session.get_providers(),
        "inputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_inputs()],
        "outputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_outputs()],
        "load_ms": round(load_ms, 2),
        "inference_ms": round(inference_ms, 2),
        "spans": spans,
    }
    report_path = args.artifacts_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("onnx_inference", report=str(report_path), **report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

