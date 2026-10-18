# Roadmap

## Phase 1 – ground prototype on a laptop

- [x] Project skeleton, configuration, aircraft profiles (V35A, high wing)
- [x] Frame sources: video file, USB camera
- [x] Candidate detector (morphology, image pyramid, sky mask)
- [x] Tracker (alpha-beta, confirmation, loss)
- [x] Threat assessment (bearing rate, robust growth, τ, hysteresis, static objects)
- [x] Callouts (EN/DE, rate limits, sector de-duplication), Windows SAPI and Piper
- [x] Synthetic test video generator, end-to-end test
- [x] ADS-B parser (SBS-1), IMU parser
- [x] Training crops from the AOT dataset (`fetch_aot_crops.py`)
- [x] Train the classifier (AOT, 82.7 % val. accuracy) and export to OpenVINO
- [ ] Run it on the Iris Xe laptop; measure on own recordings
- [x] OTA Recorder: camera + ADS-B + FLARM/OGN sessions, Windows GUI, `.exe` built by GitHub Actions
- [x] FLARM/OGN reception: OGN APRS (online and local receiver), FLARM NMEA data port
- [x] Evaluation tool `ota evaluate`: expected image positions from ADS-B/FLARM, detection range,
      automatic heading/pitch correction, auto-labelled crops, overlay video
- [x] Smart recording (traffic/motion triggers), hand-in by resumable upload (B2/R2/S3) or copy
- [x] In-flight recording: GPS/FLARM own position, own-transponder exclusion, evaluation relative
      to the GPS track with turn exclusion
- [x] Data pipeline: `ota ingest` (folders/S3, catalogue, automatic evaluation), `ota review`
      (sort crops by key press), `ota dataset` (train/val split by session)
- [ ] Collect own ground recordings at an airfield (club members with OTA Recorder)
- [ ] ADS-B cross-check in the live pipeline (cooperative / non-cooperative)

## Phase 2 – flight system on a Jetson

- [ ] GMSL2 source via GStreamer, one capture thread per camera
- [ ] Classifier on TensorRT
- [ ] IMU in the live pipeline: own rotation, high/low relative to the horizon (recording and
      evaluation already use it)
- [ ] Automatic calibration of camera attitude from horizon + IMU
- [ ] Power supply, enclosure, EMC test
- [ ] First flight tests as a pure recorder, then with callouts

## Phase 3 – extensions

- [ ] Targets below the horizon (terrain background, motion compensation)
- [ ] Temporal classifier (motion pattern: bird vs. aircraft) – main weakness of the frame classifier
- [ ] FLARM/OGN traffic in the live fusion (reception itself is done, see phase 1)
- [ ] **ADS-L** (EASA SRD-860 standard): verify that ADS-L beacons arrive via OGN receivers and the
      APRS feed; otherwise add a decoder
- [ ] GDL 90 *input* (e.g. from a Stratux with 1090 + 868 MHz sticks) as one more traffic source
- [x] In-flight evaluation with attitude: camera-mounted WitMotion IMU, GPS-aided attitude filter
- [ ] **EFB output**: Wi-Fi access point with **GDL 90** (UDP 4000; ForeFlight, SkyDemon, EasyVFR,
      Enroute, Airmate …) and **FLARM NMEA** (TCP 2000; XCSoar, Air Navigation Pro, SkyDemon via BLE).
      Ownship from GNSS, ADS-B/FLARM traffic from the SDRs, optical targets with an estimated position
      (range from apparent size, flagged as low accuracy) or as bearing-only alerts (`$PFLAU`).
      Evaluate building on Stratux instead of a separate GDL 90 server.
- [ ] Range estimate from aircraft type / wingspan
- [ ] Lens distortion, sun/glare detection
- [ ] Headless recorder box (Raspberry Pi 5 + camera + GPS + SDR) that starts with power and uploads
      at home over Wi-Fi - for school/club aircraft without a laptop
- [ ] **FPGA** stage 1 (e.g. AMD Kria): morphological filter on the pixel stream, DPU for the
      classifier - once the algorithms are stable (see [hardware.md](hardware.md#fpga-as-an-alternative))
