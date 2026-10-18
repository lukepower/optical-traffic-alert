"""Configuration model.

The configuration is split in two YAML files:

* ``config/default.yaml`` - processing parameters (detector, tracker, alerts, audio)
* ``config/aircraft/<type>.yaml`` - where the cameras sit in a given aircraft
  (mounting angles, field of view, masks for propeller and canopy frame)

``load_config`` merges both into one :class:`Config` object. Every field has a
sensible default, so a missing key never crashes the program.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class MaskRect:
    """Rectangle in normalised image coordinates (0..1) that is ignored.

    Used for the propeller disc, the canopy frame, the glareshield etc.
    """

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0


@dataclass
class CameraConfig:
    name: str = "front"
    # "file:<path>", "usb:<index>" or "gmsl:<index>" (Jetson, later)
    source: str = "usb:0"
    width: int = 3840
    height: int = 2160
    fps: float = 30.0
    # Horizontal field of view of the lens in degrees
    hfov_deg: float = 50.0
    # Mounting relative to the aircraft longitudinal axis.
    # yaw: + = pointing right, pitch: + = pointing up, roll: + = right side down
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    masks: list[MaskRect] = field(default_factory=list)
    # USB cameras: requested pixel format. "YUY2" = uncompressed (best for tiny
    # targets, needs USB 3.0 at 4K), "MJPG" = compressed, "" = camera default.
    pixel_format: str = "YUY2"
    # Manual exposure in the driver's units (DirectShow: log2 seconds, e.g.
    # -10 ~ 1 ms; Linux V4L2: 100 us steps). None = leave auto exposure on.
    exposure: float | None = None
    gain: float | None = None


@dataclass
class DetectorConfig:
    # Kernel size of the morphological filter. Objects smaller than this
    # (in pixels) are highlighted, larger structures (clouds, terrain) suppressed.
    kernel_px: int = 15
    # "ellipse" (default) or "rect". rect is ~2x faster overall, but in tests it
    # produced a false warning near a cloud edge - keep ellipse unless speed matters.
    kernel_shape: str = "ellipse"
    # Threshold = mean + k * std of the filtered image, but at least min_contrast
    sigma_k: float = 6.0
    min_contrast: float = 12.0
    min_area_px: int = 2
    max_area_px: int = 2500
    max_candidates: int = 50
    # Process only every n-th frame (1 = all). Useful on weak hardware.
    frame_stride: int = 1
    # Image pyramid. 1.0 finds distant targets (a few pixels); the coarse level
    # finds close targets that are larger than the kernel at full resolution.
    # Never drop 1.0: distant aircraft are only a few pixels wide.
    pyramid_scales: list[float] = field(default_factory=lambda: [1.0, 0.25])
    # Reject candidates whose surrounding background is not uniform (horizon
    # edge, terrain texture). Local std of the background in grey levels;
    # 0 disables. Phase 1 therefore concentrates on targets against the sky,
    # below-horizon targets need the trained classifier (docs/architecture.md).
    max_background_std: float = 18.0
    # Only search in the sky: the frame is split into bright (sky) and dark
    # (terrain) regions per frame (Otsu threshold on a blurred thumbnail),
    # the horizon edge is excluded. Works with a banked horizon, too.
    sky_only: bool = True
    # Margin (px, full resolution) kept free along the horizon edge
    sky_margin_px: int = 24
    detect_dark: bool = True
    detect_bright: bool = True


@dataclass
class ClassifierConfig:
    # "auto" (openvino if the model exists, else none), "none" or "openvino"
    backend: str = "auto"
    model_path: str = "models/classifier.xml"
    device: str = "AUTO"  # AUTO, CPU, GPU (Intel iGPU), ...
    crop_px: int = 64
    # Index of the "aircraft" class in the model output
    aircraft_class: int = 0
    # Low on purpose: the tracker filters as well, and dropping a real aircraft
    # is worse than letting a bird through (see docs/training.md, evaluation).
    min_probability: float = 0.3
    # Convert crops to grey before classification (the AOT training data is
    # monochrome). Keep true for colour cameras unless the model was trained on colour.
    grayscale: bool = True


@dataclass
class TrackerConfig:
    gate_px: float = 40.0
    min_hits: int = 5
    max_misses: int = 15
    # alpha-beta filter gains
    alpha: float = 0.6
    beta: float = 0.2
    history_s: float = 10.0


@dataclass
class ThreatConfig:
    # Track must be observed this long before an assessment is made
    min_track_age_s: float = 2.0
    # Bearing rate (deg/s) below which the bearing counts as "constant"
    max_bearing_rate_dps: float = 0.6
    # Time-to-contact limits (seconds) for the two alert levels
    advisory_tau_s: float = 40.0
    warning_tau_s: float = 20.0
    # Minimum relative size growth (1/s) to call a target "closing"
    min_growth_rate: float = 0.01
    # Growth must be a real trend: Kendall rank correlation of size over time
    # (0 = noise, 1 = steadily growing) and a minimum absolute size change (px)
    # over the growth window
    min_growth_trend: float = 0.25
    min_size_change_px: float = 1.0
    # A collision geometry must persist this long before ADVISORY/WARNING
    threat_hold_s: float = 1.0
    # ... and is kept this long after the evidence becomes weaker
    threat_release_s: float = 3.0
    # Elevation band (deg) reported as "same level"
    level_band_deg: float = 5.0
    # Announce every confirmed, moving track once as plain traffic information
    announce_all_traffic: bool = True
    # Tracks that neither move (bearing rate below this) nor grow are treated as
    # static artefacts (windscreen specks, dirt, dead pixels) and never announced.
    static_max_rate_dps: float = 0.15


@dataclass
class AudioConfig:
    # "print", "pyttsx3" (Windows SAPI), "piper"
    backend: str = "print"
    language: str = "en"  # en, de
    piper_executable: str = "piper"
    piper_model: str = "models/tts/en_US-lessac-medium.onnx"
    # Minimum time between two callouts (s)
    min_gap_s: float = 3.0
    # Repeat a callout for the same track after this many seconds
    repeat_s: float = 10.0
    volume: float = 1.0


@dataclass
class AdsbConfig:
    enabled: bool = False
    # dump1090/readsb SBS-1 (BaseStation) output
    host: str = "127.0.0.1"
    port: int = 30003
    max_age_s: float = 15.0
    # Angular tolerance to match an optical track with an ADS-B target
    match_tolerance_deg: float = 6.0


@dataclass
class OutputConfig:
    show: bool = False
    # Write an annotated video (path) or "" to disable
    record_path: str = ""
    # JSON lines event log, "" to disable
    event_log: str = "events.jsonl"


@dataclass
class Config:
    aircraft: str = "generic"
    cameras: list[CameraConfig] = field(default_factory=lambda: [CameraConfig()])
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    threat: ThreatConfig = field(default_factory=ThreatConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    adsb: AdsbConfig = field(default_factory=AdsbConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _build(cls: type, data: Any) -> Any:
    """Recursively build a dataclass from a dict, ignoring unknown keys."""
    if data is None:
        return cls()
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        ftype = f.type if not isinstance(f.type, str) else eval(f.type, globals())  # noqa: S307
        origin = getattr(ftype, "__origin__", None)
        if is_dataclass(ftype):
            kwargs[f.name] = _build(ftype, value)
        elif origin is list and getattr(ftype, "__args__", None) and is_dataclass(ftype.__args__[0]):
            kwargs[f.name] = [_build(ftype.__args__[0], v) for v in (value or [])]
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None, aircraft: str | Path | None = None,
                overrides: dict | None = None) -> Config:
    """Load processing config and (optionally) an aircraft profile.

    ``aircraft`` may be a path to a YAML file or the name of a profile in
    ``config/aircraft`` next to the main config file.
    """
    data: dict = {}
    base_dir = Path(".")
    if path:
        path = Path(path)
        base_dir = path.parent
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    aircraft = aircraft or data.get("aircraft_profile")
    if aircraft:
        ap = Path(aircraft)
        if not ap.suffix:
            ap = base_dir / "aircraft" / f"{aircraft}.yaml"
        adata = yaml.safe_load(ap.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, adata)

    if overrides:
        data = _deep_merge(data, overrides)
    data.pop("aircraft_profile", None)
    return _build(Config, data)
