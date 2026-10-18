"""The processing pipeline for one camera, plus the multi-camera runner.

    Frame -> CandidateDetector -> Classifier -> Tracker -> ThreatAssessor -> Announcer
                                                                      \\-> event log / overlay
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .audio import Announcer
from .config import CameraConfig, Config
from .detection import CandidateDetector, make_classifier
from .geometry import CameraModel
from .sensors.imu import NullImu
from .sources import Frame, FrameSource, open_source
from .tracking import Assessment, ThreatAssessor, ThreatLevel, Tracker

log = logging.getLogger(__name__)

LEVEL_COLOURS = {
    ThreatLevel.NONE: (160, 160, 160),
    ThreatLevel.TRAFFIC: (0, 200, 0),
    ThreatLevel.ADVISORY: (0, 200, 255),
    ThreatLevel.WARNING: (0, 0, 255),
}


@dataclass
class FrameResult:
    frame: Frame
    n_candidates: int
    assessments: list[Assessment] = field(default_factory=list)
    spoken: str | None = None
    proc_ms: float = 0.0


class CameraPipeline:
    def __init__(self, cfg: Config, cam: CameraConfig, width: int, height: int, classifier=None):
        self.cfg = cfg
        self.cam = cam
        self.model = CameraModel(cam, width, height)
        self.detector = CandidateDetector(cfg.detector, cam)
        self.classifier = classifier or make_classifier(cfg.classifier)
        self.tracker = Tracker(cfg.tracker, cam.name)
        self.assessor = ThreatAssessor(cfg.threat, self.model)

    def process(self, frame: Frame, ego_rates=(0.0, 0.0)) -> FrameResult:
        t0 = time.perf_counter()
        cands = self.detector.detect(frame.image)
        cands = self.classifier.score(frame.image, cands)
        tracks = self.tracker.update(cands, frame.t)
        assessments = [self.assessor.assess(tr, ego_rates) for tr in tracks]
        for tr, a in zip(tracks, assessments):
            tr.meta["assessment"] = a
        return FrameResult(frame, len(cands), assessments,
                           proc_ms=(time.perf_counter() - t0) * 1000)


def draw_overlay(img: np.ndarray, pipe: CameraPipeline, res: FrameResult) -> np.ndarray:
    out = img.copy()
    for tr in pipe.tracker.tracks:
        a: Assessment | None = tr.meta.get("assessment")
        if not tr.confirmed:
            cv2.circle(out, (int(tr.x), int(tr.y)), 6, (120, 120, 120), 1)
            continue
        colour = LEVEL_COLOURS[a.level if a else ThreatLevel.NONE]
        x, y = int(tr.x), int(tr.y)
        cv2.rectangle(out, (x - 18, y - 18), (x + 18, y + 18), colour, 2)
        if a:
            tau = f"{a.tau_s:.0f}s" if a.tau_s else "-"
            label = f"#{tr.id} {a.level.name} {a.clock}h {a.vertical} tau {tau} br {a.bearing_rate_dps:.2f}"
            cv2.putText(out, label, (x + 22, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    hud = f"t={res.frame.t:6.2f}s cand={res.n_candidates} {res.proc_ms:5.1f}ms"
    cv2.putText(out, hud, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    if res.spoken:
        cv2.putText(out, res.spoken, (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    return out


class Runner:
    """Runs one or more cameras. Cameras are processed round-robin in one thread,
    which is enough for development; on the Jetson each camera gets its own
    capture thread (see docs/roadmap.md)."""

    def __init__(self, cfg: Config, realtime: bool = False, announcer: Announcer | None = None,
                 imu=None):
        self.cfg = cfg
        self.sources: list[FrameSource] = [open_source(c, realtime=realtime) for c in cfg.cameras]
        self.pipes = [CameraPipeline(cfg, c, *s.size) for c, s in zip(cfg.cameras, self.sources)]
        self.announcer = announcer or Announcer(cfg.audio)
        self.imu = imu or NullImu()
        self.writer = None
        self.log_file = None
        if cfg.output.event_log:
            Path(cfg.output.event_log).parent.mkdir(parents=True, exist_ok=True)
            self.log_file = open(cfg.output.event_log, "w", encoding="utf-8")

    def _log(self, kind: str, payload: dict) -> None:
        if self.log_file:
            self.log_file.write(json.dumps({"kind": kind, **payload}) + "\n")

    def run(self, max_frames: int | None = None) -> list[FrameResult]:
        results: list[FrameResult] = []
        iters = [s.frames() for s in self.sources]
        stride = max(1, self.cfg.detector.frame_stride)
        n = 0
        try:
            while iters:
                for i, it in list(enumerate(iters)):
                    frame = next(it, None)
                    if frame is None:
                        iters.remove(it)
                        continue
                    if frame.index % stride:
                        continue
                    imu = self.imu.latest()
                    res = self.pipes[i].process(frame, (imu.yaw_rate_dps, imu.pitch_rate_dps))
                    res.spoken = self.announcer.offer(res.assessments, now=frame.t)
                    for a in res.assessments:
                        if a.level >= ThreatLevel.ADVISORY:
                            self._log("threat", {"t": frame.t, **a.to_dict()})
                    if res.spoken:
                        self._log("callout", {"t": frame.t, "text": res.spoken})
                    self._output(self.pipes[i], res)
                    results.append(res)
                    n += 1
                    if max_frames and n >= max_frames:
                        return results
        except KeyboardInterrupt:
            log.info("stopped by user")
        finally:
            self.close()
        return results

    def _output(self, pipe: CameraPipeline, res: FrameResult) -> None:
        out_cfg = self.cfg.output
        if not (out_cfg.show or out_cfg.record_path):
            return
        img = draw_overlay(res.frame.image, pipe, res)
        if out_cfg.record_path:
            if self.writer is None:
                h, w = img.shape[:2]
                fps = getattr(self.sources[0], "fps", pipe.cam.fps)
                self.writer = cv2.VideoWriter(out_cfg.record_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                              fps, (w, h))
            self.writer.write(img)
        if out_cfg.show:
            h, w = img.shape[:2]
            scale = min(1.0, 1280 / w)
            cv2.imshow(f"OTA {pipe.cam.name}", cv2.resize(img, None, fx=scale, fy=scale))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                raise KeyboardInterrupt

    def close(self) -> None:
        for s in self.sources:
            s.close()
        if self.writer:
            self.writer.release()
            self.writer = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        if self.cfg.output.show:
            cv2.destroyAllWindows()
