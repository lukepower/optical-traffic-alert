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

## 3. First test with a new camera (checklist)

1. **Plug into a USB 3 port** (blue, or marked SS). Check with `ota cameras` that it shows up.
2. **What does it really deliver?**

   ```powershell
   ota probe --camera usb:0
   ```

   This tries 4K/1440p/1080p in YUY2 (uncompressed) and MJPG and prints the real resolution, format and
   frame rate. Goal: **YUY2 3840×2160 with ≥ 10 fps**. A lossless 4K snapshot is written to
   `data\raw\probe_4k.png` – zoom in on the sky and check that single pixels are sharp, not blocky.
   If 4K only comes as MJPG, set `pixel_format: MJPG` in the camera config.
3. **Focus and field of view:** point the camera at a distant object (> 500 m), set the zoom to roughly
   50–60° horizontal, focus on infinity and lock the rings (tape). Measure the field of view as in
   [calibration.md](calibration.md) and note `hfov_deg`.
4. **Exposure:** against the bright sky, auto exposure often overexposes or pumps. Try manual values:

   ```powershell
   ota record --camera usb:0 --exposure -10 --seconds 10 --out data\raw\exp_test
   ```

   (DirectShow units: −10 ≈ 1 ms, −9 ≈ 2 ms, −11 ≈ 0.5 ms.) The sky should be bright but not white.
5. **How fast is this machine?** `ota bench` measures the processing speed at 4K.
6. **Live test:** `ota run --show` with the camera configured in `config/default.yaml`.

## 4. USB camera notes

Recommended for phase 1: Waveshare **IMX678 8MP USB Camera** (see the bill of materials). It is a UVC
device and needs no driver on Windows.

```powershell
ota cameras                          # finds usb:0, usb:1, ...
ota record --camera usb:0 --width 3840 --height 2160 --out data\raw\test.avi --seconds 30
ota run --video data\raw\test.avi --hfov 50 --show
```

Notes:

* The camera config requests **YUY2** (uncompressed) by default. Over USB 2.0, 4K usually only comes
  as **MJPEG** – then set `pixel_format: MJPG`. Compression costs some contrast on very small targets.
* `ota record` stores near-lossless MJPEG (`--codec mjpg`, default) or lossless FFV1 (`--codec ffv1`).
  Avoid `mp4v` for training data – it blurs targets of a few pixels. A `.timestamps.csv` next to the
  video holds the real frame times.
* Live, the pipeline always processes the **newest** frame and drops older ones if it is slower than
  the camera, so warnings never lag behind.
* `--hfov` must match the lens, otherwise clock positions and bearing rates are wrong
  (see [calibration.md](calibration.md)).
* Exposure: as short and as fixed as possible (no auto-exposure pumping). Set it in the Windows camera
  dialog or the driver settings.

## 5. Speech output into the intercom

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

## 6. ADS-B with an RTL-SDR (optional)

Tested stick: Nooelec NESDR SMArt v5 (RTL2832U + R820T2); an RTL-SDR Blog V4 works the same way.

1. **Driver:** plug in the stick, start [Zadig](https://zadig.akeo.ie/), *Options → List All Devices*,
   choose **"Bulk-In, Interface (Interface 0)"** (sometimes listed as RTL2838UHIDIR), driver
   **WinUSB**, *Replace Driver*. Windows' own DVB-T driver must not be used.
2. **dump1090 for Windows:** [gvanem/Dump1090](https://github.com/gvanem/Dump1090) (maintained, MIT,
   serves SBS/BaseStation on port 30003, map on http://localhost:8080). It is distributed as source:
   *Code → Download ZIP*, unzip to `F:\Programming\dump1090`, then double-click
   **`tools\build_dump1090.bat`**. The script finds Visual Studio / Build Tools (offers to install
   "Build Tools for Visual Studio 2022" with winget if missing), builds `dump1090.exe` with CMake and
   adapts `dump1090.cfg`.
3. **Antenna** at a window with a free view, as high as possible; ADS-B is line of sight.
4. Double-click **`tools\adsb_test.bat`**: checks the driver, starts dump1090 (`--net --interactive`)
   and lists the received aircraft with distance and direction (`ota adsb-check`).

In the OTA Recorder enter the path of `dump1090.exe` in *Traffic & sensors*; it is then started
and stopped with each recording.

Alternatively readsb in WSL2 (USB pass-through with `usbipd-win`).

## 7. IMU (optional, for flight tests)

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
