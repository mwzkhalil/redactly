"""ONNX Runtime token classifier.

Loaded once per process. Long documents are tokenised into overlapping windows so
a field near a window boundary is still seen with context on at least one side.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

import numpy as np

from ..config import get_settings
from .base import DetectedSpan, DetectorUnavailable
from .span_resolution import aggregate_window, resolve

logger = logging.getLogger(__name__)

ONNX_CANDIDATES = ("model.onnx", "onnx/model.onnx", "onnx/model_quantized.onnx")
WINDOW_STRIDE = 64
MAX_WINDOW = 512


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=-1, keepdims=True)


class OnnxTokenClassifier:
    def __init__(self) -> None:
        settings = get_settings()
        self.name = settings.model_id
        self.revision: str | None = None
        self.onnx_sha256: str | None = None
        self._lock = threading.Lock()

        try:
            import onnxruntime as ort
            from transformers import AutoConfig, AutoTokenizer
        except ImportError as error:  # pragma: no cover - dependency guard
            raise DetectorUnavailable("inference dependencies are not installed") from error

        model_dir = self._resolve_model_dir(settings.model_cache_dir, settings.model_id)
        onnx_path = self._resolve_onnx_path(model_dir)

        self.onnx_sha256 = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
        self._tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, use_fast=True)
        config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
        self._id2label = {int(key): value for key, value in config.id2label.items()}
        self._max_length = min(int(getattr(config, "max_position_embeddings", MAX_WINDOW)), MAX_WINDOW)
        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._input_names = {item.name for item in self._session.get_inputs()}
        logger.info("detector ready", extra={"event": "detector_ready", "detector": "onnx"})

    def _resolve_model_dir(self, cache_dir: Path, model_id: str) -> Path:
        from huggingface_hub import snapshot_download

        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_dir.resolve()))
        patterns = ["*.json", "*.txt", "*.model", "*.onnx", "onnx/*.onnx"]

        for offline in (True, False):
            try:
                return Path(
                    snapshot_download(
                        repo_id=model_id,
                        cache_dir=str(cache_dir.resolve()),
                        allow_patterns=patterns,
                        local_files_only=offline,
                    )
                )
            except Exception:  # noqa: BLE001 - retry online, then fail closed
                continue
        raise DetectorUnavailable(f"model {model_id} is not available locally or from the hub")

    def _resolve_onnx_path(self, model_dir: Path) -> Path:
        for candidate in ONNX_CANDIDATES:
            path = model_dir / candidate
            if path.is_file():
                return path
        discovered = sorted(model_dir.rglob("*.onnx"))
        if discovered:
            return discovered[0]
        raise DetectorUnavailable("no ONNX graph found in the downloaded model")

    def detect(self, text: str) -> list[DetectedSpan]:
        if not text.strip():
            return []
        try:
            with self._lock:
                spans = self._run(text)
        except DetectorUnavailable:
            raise
        except Exception as error:  # noqa: BLE001
            # Fail closed. A partial span list would produce a document that
            # looks redacted while leaving fields in plain view.
            raise DetectorUnavailable("detection failed") from error
        return resolve(text, spans)

    def _run(self, text: str) -> list[DetectedSpan]:
        encoded = self._tokenizer(
            text,
            return_offsets_mapping=True,
            return_overflowing_tokens=True,
            truncation=True,
            max_length=self._max_length,
            stride=WINDOW_STRIDE,
        )
        offset_windows = encoded["offset_mapping"]
        spans: list[DetectedSpan] = []

        for index in range(len(offset_windows)):
            feeds = {
                name: np.asarray([encoded[name][index]], dtype=np.int64)
                for name in self._input_names
                if name in encoded
            }
            logits = self._session.run(None, feeds)[0][0]
            probabilities = _softmax(logits)
            spans.extend(
                aggregate_window(
                    text,
                    offset_windows[index],
                    logits.argmax(axis=-1),
                    probabilities,
                    self._id2label,
                )
            )
        return spans
