# Optical Traffic Alert

**Camera-based collision warning for light aircraft** – cameras in the cockpit, a compact AI box
and spoken callouts into the intercom:

> *"Traffic, traffic, one o'clock, level, closing."*

The reference aircraft is a Beechcraft V35A Bonanza, but the system is designed for high-wing and
low-wing aircraft alike (profiles in `config/aircraft/`).

> ⚠️ **Not certified. Experimental aid.** It does not replace see-and-avoid, nor approved systems
> such as FLARM, ADS-B or TAS. See [docs/safety-and-legal.md](docs/safety-and-legal.md).

---

## Why optical?

FLARM and ADS-B only see **cooperative** traffic – aircraft that transmit themselves. Gliders without
FLARM, paragliders, ultralights, balloons and drones stay invisible. A camera sees everything that
stands out against the sky, and it never looks away – not during checklists, radio calls or
navigation.

The real challenge is **range**: a light aircraft seen head-on (~3 m cross-section) is only
**about 5 pixels** wide at 2 km with a 4K camera and a 50° field of view. At a closing speed of
100 m/s (two light aircraft head-on), 2 km leaves just 20 seconds.

## How it works

```
camera(s) ──► candidates ──► classifier ──► tracker ──► threat ──► spoken callout
              (OpenCV,        (small CNN,    (over many   assessment  (intercom)
               real time)      crops only)    frames)     (bearing + τ)
                                                ▲
                          IMU (own rotation) ───┘   ADS-B/FLARM (cross-check, later)
```

1. **Candidates** – a morphological filter (black-hat/top-hat) highlights small, high-contrast
   spots against the sky. Runs in real time even at 4K on a laptop CPU.
2. **Classifier** – a small neural network looks only at 64×64 px crops around the candidates:
   aircraft, bird or background. Runs via **OpenVINO** on Intel GPUs (Iris Xe) and later via
   TensorRT on the Jetson.
3. **Tracker** – only what is seen consistently over many frames is a target. Noise and insects
   disappear; specks on the windscreen never move.
4. **Threat assessment** – the rule every pilot learns: **constant bearing and growing size =
   collision course**. The growth rate gives an estimate of the time to contact (τ).
5. **Callout** – TCAS/FLARM style, with rules against callout flooding (see
   [docs/architecture.md](docs/architecture.md#callouts)).

| Level | Meaning | Callout |
|---|---|---|
| `TRAFFIC` | confirmed moving target, no collision geometry | "Traffic, two o'clock, high." |
| `ADVISORY` | constant bearing, growing, τ < 40 s | "Traffic, two o'clock, high, closing." |
| `WARNING` | constant bearing, growing, τ < 20 s | "Traffic, traffic, two o'clock, high, closing." |

## Quick start (Windows laptop, no camera needed)

```powershell
git clone https://github.com/lukepower/optical-traffic-alert.git
cd optical-traffic-alert
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev,tts]

# 1. Create a synthetic test video (collision target, crossing target, windscreen specks)
ota synth data/synthetic/test.mp4

# 2. Run the pipeline on it – with video window and Windows speech output
ota run --video data/synthetic/test.mp4 --show --audio pyttsx3

# 3. Tests
pytest
```

Expected output (abridged):

```
Highest alert level: WARNING
Callouts:
  - Traffic, eleven o'clock, high.
  - Traffic, traffic, one o'clock, level, closing.
```

Full setup including USB camera, RTL-SDR and intercom: [docs/setup-windows.md](docs/setup-windows.md).

## Commands

| Command | Purpose |
|---|---|
| `ota synth [file.mp4]` | create a synthetic test video + ground-truth JSON |
| `ota run --video file.mp4 [--show] [--record out.mp4]` | run the pipeline on a video |
| `ota run --aircraft v35a` | live, with the cameras from the aircraft profile |
| `ota record --camera usb:0 --out data/raw/flight1.mp4` | record raw video (data collection) |
| `ota cameras` | list connected USB cameras |

Important options of `ota run`: `--audio print|pyttsx3|piper`, `--language en|de`,
`--realtime` (play video files at real speed), `--stride 2` (every 2nd frame only, for weak
hardware), `--hfov 50` (field of view of the camera that recorded the video).

## Project layout

```
config/
  default.yaml              processing parameters (all values documented)
  aircraft/v35a.yaml        camera installation V35A (low wing)
  aircraft/high-wing-generic.yaml
src/ota/
  sources/                  frame sources: video file, USB camera, (GMSL2 on Jetson)
  detection/candidates.py   stage 1: candidates (OpenCV)
  detection/classifier.py   stage 2: crop classifier (OpenVINO)
  tracking/tracker.py       multi-target tracker (alpha-beta filter)
  tracking/threat.py        collision logic: bearing rate, growth, τ
  audio/announcer.py        callouts + speech output (console, Windows SAPI, Piper)
  sensors/adsb.py           ADS-B via dump1090/readsb (SBS-1, port 30003)
  sensors/imu.py            IMU (BNO085 via USB microcontroller)
  geometry.py               pixel → azimuth/elevation → clock position, high/low
  pipeline.py               wiring, overlay, event log
  synth.py                  test video generator
  cli.py                    command line `ota`
tools/
  fetch_aot_crops.py        stream AOT frames from S3 and cut training crops
  extract_aot_crops.py      same, from a local copy of the AOT dataset
  train_classifier.py       train the classifier + export to OpenVINO
  train_windows.bat/.ps1    one-click GPU training on Windows
tests/                      pytest, incl. end-to-end test on a synthetic video
docs/                       documentation (see below)
```

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | design, algorithms, parameters, known limits |
| [docs/setup-windows.md](docs/setup-windows.md) | laptop setup: Python, camera, audio, SDR |
| [docs/training.md](docs/training.md) | training the classifier (AOT dataset, GPU machine) |
| [docs/calibration.md](docs/calibration.md) | mounting and calibrating cameras, aircraft profiles |
| [docs/hardware.md](docs/hardware.md) | hardware, power supply, EMC, bill of materials |
| [docs/data-collection.md](docs/data-collection.md) | own recordings on the ground and in flight |
| [docs/roadmap.md](docs/roadmap.md) | phases and next steps |
| [docs/safety-and-legal.md](docs/safety-and-legal.md) | limits, liability, EASA considerations |

## Status

Phase 1 (ground prototype on a laptop): the pipeline runs end to end on video files and USB cameras,
flags the collision target in the synthetic scenario as `WARNING` and suppresses windscreen specks.
A first crop classifier has been trained on the AOT dataset (82.7 % validation accuracy, see
[docs/training.md](docs/training.md#4-evaluation)); the pipeline uses it automatically when
`models/classifier.xml` is present. See [docs/roadmap.md](docs/roadmap.md).

## Contributing

Issues and pull requests are welcome. Please run `pytest` before submitting, keep code comments and
documentation in English, and remember that this is safety-related software: prefer conservative
changes and describe how you tested them.

## License

MIT – see [LICENSE](LICENSE).
