# Mounting and calibrating the cameras

For a callout like "two o'clock, high" to be correct, the system must know where each camera is
looking and how wide its field of view is. That is defined in the aircraft profile
`config/aircraft/<type>.yaml`.

## Fields per camera

| Field | Meaning |
|---|---|
| `name` | free text, shown in the log and overlay |
| `source` | `usb:0`, `file:…`, later `gmsl:0` |
| `width`, `height`, `fps` | requested resolution |
| `hfov_deg` | horizontal field of view of the lens |
| `yaw_deg` | horizontal direction: 0 = ahead, + = right, − = left |
| `pitch_deg` | vertical direction: + = up |
| `roll_deg` | rotation about the optical axis (+ = right side down) |
| `masks` | rectangles in normalised coordinates (0..1) that are ignored |

## Placement

**Low wing (V35A):**

* Front: centred on the glareshield, 0 to +5° up, narrow lens (~50°). The propeller disc is in the
  image – with a short exposure, blades appear as frozen bars in single frames. The tracker bridges
  this; add a mask if needed.
* Sides: at the side windows, 45–60° to the side and forward, tilted slightly up (+5°), because the
  wing blocks the view downwards.

**High wing:**

* Front as above.
* Sides tilted slightly down (−3°), because the wing blocks the view upwards; mask the wing root and
  door frame at the top.

**Against reflections:** a matt black sun shade right against the windscreen; optionally a
polarising filter. Keep the windscreen clean – specks are suppressed but still cost contrast.

## Measuring the field of view

Manufacturer figures are often diagonal or imprecise. A simple measurement:

1. Point the camera at an object of known width `B` at a known distance `D` (e.g. a hangar door).
2. Measure its width in the image in pixels: `b`.
3. Focal length in pixels: `f = b * D / B`
4. `hfov = 2 * atan((image_width / 2) / f)`

Example: 4K image (3840 px), a 12 m wide door at 100 m appears 1000 px wide →
f = 1000 · 100 / 12 = 8333 px → hfov = 2 · atan(1920 / 8333) ≈ 26°.

## Determining the mounting angles

**On the ground:** putting a nose-wheel aircraft into flight attitude is awkward; for phase 1 it is
enough to align the camera so the centre line (spinner or nose) is in the image centre →
`yaw_deg: 0`. Side cameras: wing tip or a reference point in a known direction.

**In flight (automatic, later):** the horizon in the image gives the camera's pitch and roll relative
to the IMU. A self-calibration in calm straight-and-level flight is planned.

## Masks

An overlay video shows where candidates appear:

```powershell
ota run --video data\raw\ground1.mp4 --record out\ground1_overlay.mp4
```

Where grey circles (unconfirmed tracks) sit permanently on frames, glareshield or propeller, add a
rectangle to `masks`. Coordinates are fractions of the image width/height:
`{x0: 0.0, y0: 0.88, x1: 1.0, y1: 1.0}` = bottom 12 % of the image.

## New aircraft profile

Copy `config/aircraft/v35a.yaml`, rename it, adjust the values, then:

```powershell
ota run --aircraft my-aircraft --show
```
