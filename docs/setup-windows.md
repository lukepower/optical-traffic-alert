# Setup on a Windows laptop

Goal: development and ground tests on an ordinary laptop – also **without an NVIDIA GPU**
(e.g. Intel Iris Xe).

## 1. Python and the project

1. Install Python 3.11 or 3.12 from [python.org](https://www.python.org/downloads/windows/)
   (tick "Add python.exe to PATH").
2. Install Git: [git-scm.com](https://git-scm.com/download/win).
3. Clone the project and create a virtual environment:

```powershell
cd F:\Programming
git clone https://github.com/lukepower/optical-traffic-alert.git
cd optical-traffic-alert
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .[dev,tts,openvino,serial]
```

Extras at a glance:

| Extra | Contents | Needed for |
|---|---|---|
| `dev` | pytest | tests |
| `tts` | pyttsx3 | speech output with Windows voices |
| `openvino` | OpenVINO runtime | as soon as a trained model is available |
| `serial` | pyserial | IMU over USB |
| `train` | Ultralytics | training only, on the GPU machine |

4. Smoke test:

```powershell
pytest
ota synth data\synthetic\test.mp4
ota run --video data\synthetic\test.mp4 --show --audio pyttsx3
```

Press `q` in the window to quit.

## 2. Intel graphics (Iris Xe) with OpenVINO

The Iris Xe has no dedicated graphics memory – the 128 MB often shown are only the fixed
allocation; the rest is taken dynamically from system RAM. That is plenty for the small classifier.

* Install a current Intel graphics driver (Intel Driver & Support Assistant).
* Check which devices OpenVINO sees:

```powershell
python -c "import openvino as ov; print(ov.Core().available_devices)"
```

Expected: `['CPU', 'GPU']`. Then set `classifier.device: GPU` or `AUTO` in `config/default.yaml`.

## 3. USB camera

Recommended for phase 1: Waveshare **IMX678 8MP USB Camera** (see the bill of materials). It is a UVC
device and needs no driver on Windows.

```powershell
ota cameras                          # finds usb:0, usb:1, ...
ota record --camera usb:0 --width 3840 --height 2160 --out data\raw\test.mp4 --seconds 30
ota run --video data\raw\test.mp4 --hfov 50 --show
```

Notes:

* Over USB 2.0 high resolutions only come as **MJPEG** – this is configured. Compression costs some
  contrast on very small targets; fine for first tests.
* `--hfov` must match the lens, otherwise clock positions and bearing rates are wrong
  (see [calibration.md](calibration.md)).
* Exposure: as short and as fixed as possible (no auto-exposure pumping). Set it in the Windows camera
  dialog or the driver settings.

## 4. Speech output into the intercom

**Quick:** `--audio pyttsx3` uses the built-in Windows voices. Install an English voice under
*Settings → Time & language → Language* (for `en`).

**Better:** [Piper](https://github.com/rhasspy/piper) – neural offline TTS, much clearer, and runs
the same way on the Jetson later.

1. Download and unpack the Windows release of Piper, e.g. to `C:\tools\piper\`.
2. Download a voice (`.onnx` + `.onnx.json`), e.g. `en_US-lessac-medium`, into `models\tts\`.
3. In `config/default.yaml`:

```yaml
audio:
  backend: piper
  piper_executable: C:/tools/piper/piper.exe
  piper_model: models/tts/en_US-lessac-medium.onnx
```

**Connecting to the intercom:** headphone output or USB audio adapter → aux input of the intercom
(music in). If there is hum, add a ground-loop isolator. Set the volume so callouts are clearly above
engine noise but below radio traffic.

## 5. ADS-B with an RTL-SDR (optional)

1. Plug in the RTL-SDR Blog V4.
2. With [Zadig](https://zadig.akeo.ie/) install the WinUSB driver for "Bulk-In, Interface (Interface 0)".
3. Start a Windows build of dump1090 that offers SBS output on port 30003, e.g.:

```powershell
dump1090.exe --interactive --net
```

Alternatively readsb in WSL2 (USB pass-through with `usbipd-win`).

4. Set `adsb.enabled: true` in `config/default.yaml`. (Fusion in the pipeline follows in a later
   version; the parser is done and tested.)

## 6. IMU (optional, for flight tests)

BNO085 on a small USB microcontroller (RP2040 or ESP32) that prints lines in the format

```
IMU,<yaw_rate>,<pitch_rate>,<roll_rate>,<roll_deg>,<pitch_deg>
```

at 50–100 Hz. Find the port in Device Manager (e.g. `COM5`).

## Troubleshooting

| Problem | Fix |
|---|---|
| `ota` not found | is the virtual environment active? `.venv\Scripts\activate` |
| camera does not open | close other apps (Teams, Camera app); `ota cameras` |
| only 1920×1080 instead of 4K | the camera may offer 4K only at lower fps; try `--fps 15` |
| very slow | `--stride 2`, `pyramid_scales: [1.0]`, lower resolution for testing |
| no speech | check with `--audio print` that callouts are generated; then the audio backend |
