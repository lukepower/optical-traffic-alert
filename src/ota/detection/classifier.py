"""Stage 2: crop classifier.

Decides for each candidate crop whether it is an aircraft or something else
(bird, insect or dirt on the windscreen, sensor noise, terrain texture).

Backends
--------
* ``auto``     - ``openvino`` if ``model_path`` exists and OpenVINO is installed, else ``none`` (default)
* ``none``     - accepts every candidate (score 1.0).
* ``openvino`` - runs an OpenVINO IR model (``.xml`` + ``.bin``). This is the
  format Ultralytics exports with ``format="openvino"`` and it runs on Intel
  CPUs and Intel iGPUs (Iris Xe) without CUDA. On the Jetson the same network
  will be exported to TensorRT instead.

Model contract: input 1x3xSxS float32 RGB scaled to 0..1, output 1xN class
scores (logits or probabilities). See docs/training.md.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from ..config import ClassifierConfig
from .candidates import Candidate, crop

log = logging.getLogger(__name__)


class Classifier:
    def score(self, image: np.ndarray, candidates: list[Candidate]) -> list[Candidate]:
        """Fill in ``Candidate.score`` and return the candidates that pass."""
        raise NotImplementedError


class PassthroughClassifier(Classifier):
    def score(self, image, candidates):
        for c in candidates:
            c.score = 1.0
        return candidates


def _softmax(x: np.ndarray) -> np.ndarray:
    if np.all(x >= 0) and abs(float(x.sum()) - 1.0) < 1e-3:
        return x  # already probabilities
    e = np.exp(x - x.max())
    return e / e.sum()


class OpenVinoClassifier(Classifier):
    def __init__(self, cfg: ClassifierConfig):
        try:
            import openvino as ov
        except ImportError as e:  # pragma: no cover - optional dependency
            raise ImportError("Install the OpenVINO extra: pip install -e .[openvino]") from e
        self.cfg = cfg
        core = ov.Core()
        model = core.read_model(cfg.model_path)
        self.compiled = core.compile_model(model, cfg.device)
        self.output = self.compiled.output(0)
        log.info("OpenVINO classifier %s on %s", cfg.model_path, cfg.device)

    def score(self, image, candidates):
        if not candidates:
            return []
        s = self.cfg.crop_px
        batch = []
        for c in candidates:
            patch = crop(image, c, s)
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            batch.append(patch.transpose(2, 0, 1))
        accepted = []
        # One crop per inference call keeps the model input static (1x3xSxS)
        for c, x in zip(candidates, batch):
            out = self.compiled([x[None]])[self.output][0]
            p = _softmax(np.asarray(out, dtype=np.float32).ravel())
            c.score = float(p[self.cfg.aircraft_class])
            if c.score >= self.cfg.min_probability:
                accepted.append(c)
        return accepted


def make_classifier(cfg: ClassifierConfig) -> Classifier:
    if cfg.backend == "auto":
        # Use the trained model if it is there and OpenVINO is installed,
        # otherwise run without classifier (models/ is not in the repository).
        from pathlib import Path
        try:
            import openvino  # noqa: F401
            has_ov = True
        except ImportError:
            has_ov = False
        if has_ov and Path(cfg.model_path).exists():
            return OpenVinoClassifier(cfg)
        log.warning("classifier: no model at %s or OpenVINO missing - running without classifier",
                    cfg.model_path)
        return PassthroughClassifier()
    if cfg.backend == "none":
        return PassthroughClassifier()
    if cfg.backend == "openvino":
        return OpenVinoClassifier(cfg)
    raise ValueError(f"Unknown classifier backend: {cfg.backend!r}")
