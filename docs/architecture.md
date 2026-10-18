# Architecture

This document describes how the pipeline is built, why it is built that way and what each
parameter does. All parameters, with their defaults, are in `config/default.yaml` and
`src/ota/config.py`.

## Overview

```
             ┌──────────────────────────── per camera ─────────────────────────┐
FrameSource ─┤ CandidateDetector → Classifier → Tracker → ThreatAssessor ──────┼─► Announcer ─► intercom
             └─────────────────────────────────────────────────────────────────┘        │
                         IMU (own rotation) ───────────────┘                            └─► event log (JSONL)
```

Each stage is a small class with a clear interface. The same pipeline therefore runs

* on a Windows laptop with a video file or USB camera (phase 1) and
* later on a Jetson with three GMSL2 cameras (phase 2),

with nothing changing except the frame source and the classifier backend.

## Design principles

1. **Cheap stages first.** Classical image processing reduces an 8-megapixel frame to a handful of
   candidates; only those go through the neural network. A laptop with Intel graphics is enough.
2. **Time beats single frames.** A target at 2 km is a few pixels. Single frames are unreliable,
   consistency over many frames is not.
3. **False alarms cost more than a missed target.** A system that keeps crying wolf gets switched
   off. Hence: hysteresis, minimum observation time, suppression of static objects, callout rules.
4. **Everything measurable.** Every event goes into the JSONL log; every frame can be recorded with
   an overlay.

## Stage 0 – frame sources (`src/ota/sources`)

| Source | Configuration | Notes |
|---|---|---|
| Video file | `file:path.mp4` | timestamps from the file's frame rate; `--realtime` plays at real speed |
| USB camera | `usb:0` | OpenCV, DirectShow on Windows; high resolutions usually only as MJPEG |
| GMSL2 (Jetson) | `gmsl:0` | planned, via GStreamer |

All sources yield `Frame(camera, index, t, image)`. `t` is time in seconds – for video files the
**video time**, not the wall clock. All time-dependent rules (tracker, threat, callouts) use this
time, so a video is assessed identically whether the computer processes it faster or slower than
real time.

## Stage 1 – candidates (`detection/candidates.py`)

**Idea:** a distant aircraft is a small spot, darker (or, with sun glint, brighter) than its
surroundings. A morphological *closing* with a kernel of `kernel_px` removes all objects smaller
than the kernel – what remains is the background. Background − image (black-hat) shows exactly the
small dark objects; opening/top-hat does the same for bright ones.

Per frame:

1. **Sky mask** (`sky_only`): a blurred thumbnail (1/8) is split into bright (sky) and dark
   (terrain) with Otsu's method. The border is eroded by `sky_margin_px` so the horizon edge itself
   – a strong, textured edge – is excluded. Works with a banked horizon, too.
2. **Image pyramid** (`pyramid_scales: [1.0, 0.25]`): full resolution finds distant targets (a few
   pixels). Close targets are larger than the kernel and break into parts (fuselage, wings) at full
   resolution; the quarter-resolution level finds them as a whole. Each level only reports the object
   sizes it handles well; fine-level fragments that lie inside a coarse detection are dropped.
3. **Threshold**: mean + `sigma_k` × standard deviation of the filter response, at least
   `min_contrast`. The threshold therefore adapts to brightness and noise automatically.
4. **Connected components** with a 3×3 dilation (joins parts of one object), filtered by
   `min_area_px` / `max_area_px`.
5. **Background check** (`max_background_std`): if the background around a candidate is not uniform
   (horizon, cloud edge, terrain), the candidate is rejected.
6. **Effective size**: the integrated filter response divided by its peak gives an effective area.
   It changes **smoothly** with distance, whereas the pixel count of a thresholded blob jumps between
   a few values. This is essential for the τ estimate.

Static masks per camera (propeller disc, glareshield, window frames) come from the aircraft profile
(`masks`, normalised rectangles 0..1).

**Phase 1 limitation:** with `sky_only: true`, only targets against the sky are searched. Targets
below the horizon in front of terrain are much harder (lots of small structure) and need the trained
classifier and, in flight, motion compensation. In mountainous terrain this matters – see the roadmap.

## Stage 2 – classifier (`detection/classifier.py`)

A small CNN rates 64×64 px crops around each candidate.

* Classes (alphabetical, as Ultralytics sorts them): `aircraft` (0), `background` (1), `bird` (2)
* Input: 1×3×64×64, RGB, 0..1; output: class scores (logits or probabilities)
* Backends: `auto` (default: OpenVINO if `models/classifier.xml` exists, otherwise none),
  `none` (pass all candidates), `openvino` (Intel CPU/iGPU), later TensorRT on the Jetson
* Threshold: `min_probability`

Training: [training.md](training.md).

## Stage 3 – tracker (`tracking/tracker.py`)

* One **alpha-beta filter** (constant velocity) per track in image coordinates (`alpha`, `beta`).
* **Association**: greedy by distance within a gate (`gate_px`) that grows with every missed frame.
* A track is **confirmed** after `min_hits` associations and deleted after `max_misses` frames
  without a detection.
* Each track keeps its history (`history_s` seconds): time, position, size, contrast.

Deliberately simple and dependency-free. If crossing targets become a problem, the association can
be replaced by the Hungarian algorithm (scipy) – the interface stays the same.

## Stage 4 – threat assessment (`tracking/threat.py`)

For each confirmed track, over sliding windows:

| Quantity | Computation | Meaning |
|---|---|---|
| Bearing rate | slope of azimuth and elevation (3 s, least squares), minus own rotation from the IMU | ≈ 0 → constant bearing |
| Growth rate | Theil-Sen slope of size / mean size (5 s) | relative size increase per second |
| Trend | Kendall rank correlation size ↔ time | 1 = growing steadily, ≈ 0 = noise |
| τ | 1 / growth rate | estimated time to contact |

**Why robust statistics?** Blob sizes of 2–5 px jump between a few values. Ordinary regression finds
random "growth" in that – in tests with terrain clutter this caused constant false warnings.
Theil-Sen and Kendall are insensitive to outliers and jumps.

τ is only accepted if

* the trend is ≥ `min_growth_trend` **and**
* the size grew by at least `min_size_change_px` within the window **and**
* the growth rate is > `min_growth_rate`.

**Levels:**

* `NONE` – too young (`min_track_age_s`) or **static**: does not move (`static_max_rate_dps`) and has
  never grown. These are windscreen specks, insect remains, dead pixels.
* `TRAFFIC` – confirmed, moving target.
* `ADVISORY` – constant bearing (≤ `max_bearing_rate_dps`) and τ ≤ `advisory_tau_s`.
* `WARNING` – constant bearing and τ ≤ `warning_tau_s`.

**Hysteresis:** a collision geometry must persist for `threat_hold_s` before it is reported, and a
reported threat is held for `threat_release_s` even if single frames are weaker.

**Physical limit:** a target on a collision course barely grows measurably for a long time. At 3 px
and τ = 30 s it grows by 0.1 px per second. The warning therefore typically comes 10–20 s before the
encounter – enough to avoid, but not earlier. A longer focal length (narrower FOV) moves this forward.

### Own rotation (IMU)

When the own aircraft turns, all targets move in the image. Without correction, every turn would
destroy or fake a "constant bearing". The IMU provides yaw and pitch rates; they are added to the
measured bearing rate (`ego_rates_dps`). On the ground with a fixed camera the IMU is not needed
(`NullImu`).

## Geometry (`geometry.py`)

Pinhole camera model: focal length in pixels from `hfov_deg` and image width; mounting from
`yaw_deg`, `pitch_deg`, `roll_deg`. Result: azimuth (0° = ahead, + = right) and elevation.

* Clock position: 30° per hour, 12 = ahead, 3 = right, 9 = left
* high/low/level: elevation outside ±`level_band_deg` (currently relative to the aircraft's
  longitudinal axis; with the IMU attitude later relative to the horizon)

Lens distortion is ignored. With 40–60° lenses the error is small compared with the resolution of a
clock position (30°).

## Callouts

`audio/announcer.py` decides **what** to say and **when**; speech runs on its own thread so the
vision pipeline never waits for audio.

Rules:

* at most one callout every `min_gap_s` seconds – except for `WARNING`
* the same track is repeated only if its level rises, its clock position changes or `repeat_s` has
  passed
* the same **sector** (clock position + high/level/low) is not repeated within `repeat_s` unless the
  level rises – a target briefly split into two tracks does not cause a double callout
* with several candidates, the highest level wins, then the smallest τ

Speech output:

| Backend | Platform | Note |
|---|---|---|
| `print` | everywhere | console only |
| `pyttsx3` | Windows | uses the built-in SAPI voices, `pip install -e .[tts]` |
| `piper` | Windows, Linux, Jetson | neural offline TTS, very intelligible, needs the program + a voice |

Languages: `en` (default, aviation phraseology) and `de`.

## ADS-B / FLARM (`sensors/adsb.py`)

dump1090 or readsb provide the SBS-1 format on TCP port 30003. `SbsParser` keeps the latest state per
ICAO address; `relative_position()` computes bearing relative to own heading, distance and altitude
difference.

All cooperative sources are normalised into one record, `TrafficReport` (`sensors/traffic.py`):

| Source | Module | Transport |
|---|---|---|
| ADS-B | `sensors/adsb.py` (`sbs_report`) | dump1090 SBS, TCP 30003 |
| FLARM/OGN online | `sensors/ogn.py` | APRS-IS `aprs.glidernet.org:14580`, range filter |
| FLARM/OGN local receiver | `sensors/ogn.py` | `ogn-decode` APRS lines, TCP 50001 |
| FLARM device | `sensors/flarm.py` | NMEA `$PFLAA`/`$GPRMC`/`$GPGGA`, serial or TCP |

`sensors/feeds.py` provides the reconnecting TCP/serial line reader. Pressure altitudes
(ADS-B) are flagged `alt_ref: baro` and converted with QNH during evaluation. OGN beacons carry
their own UTC position time, which is used instead of the receive time.

Uses:

1. **Cross-check**: optical track and ADS-B target within `match_tolerance_deg` → cooperative
   traffic, probably already on the EFB/FLARM display. Callout may be softened.
2. **Ground truth for tests**: ADS-B provides real distances – used to validate the optical τ
   estimate in test flights.

3. **Recording and evaluation**: the session recorder (`recorder/`) logs all sources next to the
   video. `evaluate.py` projects every aircraft into the image (`CameraModel.direction_to_pixel`,
   WGS84 look angles), matches detections and estimates the camera heading/pitch error. See
   [recorder.md](recorder.md).

## Event log

`output.event_log` (default `out/events.jsonl`), one JSON line per event:

```json
{"kind": "threat", "t": 17.7, "track_id": 21, "camera": "front", "level": "WARNING",
 "azimuth_deg": 20.0, "elevation_deg": 1.0, "clock": 1, "vertical": "level",
 "bearing_rate_dps": 0.002, "growth_rate": 0.18, "tau_s": 5.6, "size_px": 3.2}
{"kind": "callout", "t": 17.7, "text": "Traffic, traffic, one o'clock, level, closing."}
```

## Performance

`ota bench` measures the detector on a synthetic 4K frame. In the cloud test environment
(2 vCPU) the default settings take about **115 ms per 4K frame (≈ 8.6 fps for one camera)**:

| Variant | ms / 4K frame |
|---|---|
| default (ellipse kernel, pyramid, sky mask) | ~115 |
| full resolution only | ~105 |
| rect kernel | ~80 |
| without sky mask | ~250 |

Two optimisations made this possible: connected components are only labelled in the regions of a
coarse grid that actually contain candidate pixels (a full-frame labelling at 4K costs ~15 ms per
call), and the background-uniformity check is evaluated locally per candidate instead of with
full-frame box filters. The rect kernel is faster still but produced a false warning near a cloud
edge in the synthetic test, so ellipse stays the default.

Knobs: `frame_stride`, `kernel_shape`, `pyramid_scales: [1.0]`, masks.

## Known limitations (phase 1)

* only targets against the sky (`sky_only`)
* the frame classifier confuses small birds and aircraft (~30 % of birds pass at the default threshold)
* no lens distortion, no attitude correction for high/low
* cameras are processed one after another in a single thread
* range drops significantly against the sun and in strong haze
