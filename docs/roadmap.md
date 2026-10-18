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
- [ ] Train the classifier, deploy with OpenVINO on the Iris Xe
- [ ] Collect own ground recordings at an airfield
- [ ] ADS-B cross-check in the pipeline (cooperative / non-cooperative)
- [ ] Evaluation script: event log against ADS-B ground truth

## Phase 2 – flight system on a Jetson

- [ ] GMSL2 source via GStreamer, one capture thread per camera
- [ ] Classifier on TensorRT
- [ ] IMU integration: own rotation, high/low relative to the horizon
- [ ] Automatic calibration of camera attitude from horizon + IMU
- [ ] Power supply, enclosure, EMC test
- [ ] First flight tests as a pure recorder, then with callouts

## Phase 3 – extensions

- [ ] Targets below the horizon (terrain background, motion compensation)
- [ ] Temporal classifier (motion pattern: bird vs. aircraft)
- [ ] FLARM reception (868 MHz, OGN) in the fusion
- [ ] Range estimate from aircraft type / wingspan
- [ ] Lens distortion, sun/glare detection
