# Data collection

Own recordings are the most valuable material: they show exactly the camera, windscreen, light and
landscape the system will have to work with.

## On the ground

**Location:** apron or edge of an airfield with traffic (e.g. looking towards final approach), with
the aerodrome operator's permission. Arriving traffic is ideal test material: a target coming
straight at the camera and growing – exactly the collision geometry.

Use the **OTA Recorder** ([recorder.md](recorder.md)): it records the video together with ADS-B,
FLARM and OGN positions and the station/camera geometry into one session folder. The headless
equivalent is

```powershell
ota session --station LIPB --lat 46.4603 --lon 11.3264 --elev 241 --heading 190 --pitch 3 --hfov 50 --qnh 1018 --seconds 1800
```

Video-only recording is still possible with `ota record` (near-lossless MJPEG plus a
`.timestamps.csv`). 4K at 15 fps is roughly 1–2 GB per minute, so use a fast SSD.

Note weather, sun position and anything notable (non-cooperative traffic, birds) in the
*Notes* field.

## In flight (phase 1: laptop as recorder)

* Fix the camera behind the windscreen, with a sun shade.
* Stow the laptop securely; nothing may come loose in turbulence.
* Record only, no warnings – evaluation happens on the ground.
* Record traffic in parallel (OTA Recorder in *aircraft* mode, with a USB GPS receiver or the FLARM
  data port for the own position) for ground truth. See
  [recorder.md](recorder.md#in-the-aircraft-eg-club-or-school-aircraft).
* Operation must not interfere with flying: start recording before engine start, stop after shutdown.

## Evaluation

```powershell
ota evaluate D:\OTA-Sessions\20261020-074512Z_LIPB --overlay
```

* `eval/report.md`: detection range per aircraft, probability of detection by range, angular
  error, corrected camera heading/pitch
* `eval/overlay.mp4`: expected positions (yellow) vs. detections (green / magenta = no cooperative
  aircraft)
* `eval/crops/aircraft/`: auto-labelled aircraft crops from our own camera → training data
* `eval/crops/review/`: tracks without a cooperative aircraft → label by hand (non-cooperative
  aircraft, bird, background)

For a plain video without traffic data, `ota run --video … --record overlay.mp4` still works.

## Privacy

Recordings may show people, registrations and private property. Raw videos stay local (`data/` is in
`.gitignore`); at most selected excerpts are published.
