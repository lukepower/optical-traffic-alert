# Hardware

The full bill of materials with prices (October 2026, EUR) is in
[hardware/bill-of-materials.xlsx](hardware/bill-of-materials.xlsx) (spreadsheet in German).

## Phase 1 – laptop (ground tests)

| Component | Example | approx. price |
|---|---|---|
| Laptop | existing (Intel Iris Xe is enough) | – |
| USB camera | **in use:** Svpro 4K USB 3.0 camera, Sony IMX415, YUYV, CS-mount 5–50 mm zoom (Amazon B0CZDHVYLM) | €193 |
| SDR for ADS-B | **in use:** Nooelec NESDR SMArt v5 (RTL2832U + R820T2, 0.5 ppm TCXO, 3 antennas); RTL-SDR Blog V4 is equivalent | €58 |
| Second SDR for FLARM/OGN (868 MHz) | optional; needs a Raspberry Pi with OGN software (no Windows decoder), or use OGN online / a FLARM data port instead ([recorder.md](recorder.md#getting-flarm-locally-second-sdr-stick)) | ~€50 + Pi |
| Audio | USB audio adapter, intercom cable, ground-loop isolator | €65 |

This is enough to develop and ground-test the whole pipeline. The zoom lens is for finding the best
field of view on the ground; for flight a fixed 6–8 mm lens is needed (vibration).

## Camera requirements

What matters is not the megapixel count but the **angular resolution**: how many pixels cover one
degree of the sky. A distant aircraft must still be 3–5 pixels wide.

| Property | Requirement | Why |
|---|---|---|
| Resolution | 3840 × 2160 (8 MP, "4K") or more | together with the lens: ≥ 60 px per degree |
| Field of view | 50–60° horizontal (exchangeable M12 or CS lens) | 3840 px / 60° = 64 px/°; wide-angle lenses (90–120°) waste range |
| Sensor | Sony STARVIS 2 or similar, pixel size ≥ 1.5 µm (e.g. IMX678, IMX585, IMX415, IMX477) | contrast of tiny targets in haze |
| Colour / mono | **either**; mono is better (more light per pixel, no demosaicing) | the pipeline works on grey anyway; the classifier converts colour crops to grey (`classifier.grayscale`) |
| Shutter | **rolling shutter is fine** | global shutter at 4K is expensive; use a short, fixed exposure (< 1 ms in daylight) |
| Interface | USB 3.0 UVC, uncompressed (YUY2) preferred; USB 2.0 MJPEG acceptable for tests | compression blurs targets of a few pixels |
| Frame rate | ≥ 10 fps at 4K | collision geometry is evaluated over seconds |
| Focus | fixed / manual, set to infinity – no autofocus | autofocus hunts on an empty sky |
| Controls | manual exposure and gain via UVC | avoid auto-exposure pumping against bright sky |

Lens focal length for 50–60° HFOV depends on the sensor width: about 6–8 mm for a 1/1.8" sensor
(IMX678), about 10–12 mm for a 1/1.2" sensor (IMX585).

### Test kit: GPS and attitude sensor

For in-flight tests with the laptop recorder:

| Component | Example | approx. price | Why |
|---|---|---|---|
| USB GPS receiver | any u-blox USB "GPS mouse" (e.g. VK-172 with u-blox 7, or an M8/M10 USB puck) | €15–40 | own position and track; NMEA, appears as a COM port |
| Attitude sensor (IMU) | **WitMotion WT901C / HWT901B with USB** (or TTL version + USB-TTL adapter) | €30–70 | camera attitude in turns; up to 200 Hz, ready-made, no soldering |

**Mount the IMU rigidly on the camera bracket**: X axis (arrow on the module) along the optical axis,
Z axis up in the image. Then it measures the **camera's** attitude, including how the mount
sits in the aircraft. Set it once to 100–200 Hz and 115200 baud with the WitMotion PC software.
Check axes and signs with `ota imu-check --port COM6@115200`: tilting the camera nose up must give
positive pitch, lowering its right side must give positive roll.

The module's own roll/pitch are not used for the evaluation. Like any gravity-referenced AHRS, it
slowly "levels" in a sustained coordinated turn. The evaluation computes the attitude from the raw
gyro and accelerometer data, corrected with the GPS velocity, and holds the bank angle correctly
([architecture.md](architecture.md#camera-attitude-in-flight-imu--gps)).

Without a magnetometer the IMU has no heading of its own. The heading comes from the GPS track; a
constant wind correction angle is absorbed by the automatic heading correction.

## Phase 2 – flight system

| Component | Example | Note |
|---|---|---|
| Computer | NVIDIA Jetson Orin NX 16 GB (carrier with 9–20 V input) | 15–40 W depending on mode |
| Cameras | 3 × GMSL2, e.g. Basler ace 2 a2A3536-37mgm (IMX676, 12.5 MP, mono) | powered over coax (PoC) |
| Deserializer | GMSL2 board for up to 4 cameras (e.g. Forecr DSADDON-GMSL-NX) | |
| Cables | FAKRA coax, 3 m | |
| IMU | Adafruit BNO085 | via USB microcontroller |
| GNSS | u-blox M10 | own position for ADS-B fusion |
| Storage | NVMe 1 TB | recording |
| Enclosure | aluminium with fan | cockpits get hot in summer |

**Why GMSL2?** MIPI cables only work up to ~30 cm. Cameras at different places in the cockpit need
long, interference-resistant cables – GMSL2 carries video and power over one coax cable.

**Why monochrome?** Without a colour filter array a mono sensor gets more light per pixel and more
effective resolution – both matter more than colour for targets of a few pixels.

**Global vs. rolling shutter:** global shutter avoids propeller artefacts and vibration distortion
but is expensive at high resolution. Rolling shutter with a short exposure works in practice;
propeller artefacts are masked or bridged by the tracker.

### Jetson options and prices (October 2026)

NVIDIA raised Jetson prices by up to 101 % in July 2026
([CNX Software](https://www.cnx-software.com/2026/07/22/nvidia-increases-the-price-of-jetson-modules-and-devkits-by-up-to-101/)).
Module prices are list prices for 1000 units; single modules in shops cost more.

| | before | now |
|---|---|---|
| Orin NX 16 GB module | $599 | **$999** |
| Orin NX 8 GB module | $399 | $649 |
| Orin Nano Super Developer Kit | $249 | **$399** |
| AGX Orin Developer Kit | $1,999 | $3,499 |

Carrier boards with GMSL2 for three cameras:

| Option | GMSL2 inputs | Power input | Price | Note |
|---|---|---|---|---|
| [Seeed reComputer Robotics J4012 + GMSL extension](https://eu.robotshop.com/de/products/seeedstudio-recomputer-robotics-j4012-mit-gmsl-extension-mit-jetson-orin-nx-16gb) (Orin NX 16 GB, complete with enclosure) | via extension board – check count | check | ~€2,070 | quickest route to a complete unit |
| [Forecr GMSL2 camera board](https://www.forecr.io/products/gmsl2-camera-board-for-orin-nx-orin-nano) + Forecr carrier + module | 4, Mini-FAKRA, PoC | depends on carrier | board €430 | modular, cheaper |
| [Connect Tech Hadron GMSL](https://connecttech.com/product/hadron-gmsl-carrier-for-nvidia-jetson-orin-nx/) | **2** | 9–60 V | on request | too few inputs for 3 cameras |
| [Syslogic RPC RS A4NX GMSL](https://www.syslogic.com/jetson-orin-nx/rugged-computer-rpc-rs-a4nx-super-gmsl) (rugged, IP67) | 4 | 9–45 V | on request | ideal for aircraft use, industrial price |

**Camera, deserializer board and driver must fit together.** Choose cameras that the board
vendor supports with a ready driver. Otherwise each new camera means driver work. Few 4K mono
GMSL2 cameras exist; the choice of cameras limits the choice of board more than the other way round.

**Recommendation:** buy only after `ota bench` on real recordings. A single camera for first
flights works with an Orin Nano Super kit or simply a laptop / Raspberry Pi 5 as recorder.

### FPGA as an alternative

An FPGA (e.g. AMD Kria K26/KV260, Zynq UltraScale+, [$249 starter kit](https://www.amd.com/en/products/system-on-modules/kria/k26/kv260-vision-starter-kit.html)
with 3 MIPI camera inputs) is a good fit for **stage 1**. The morphological filter and threshold can
run on the pixel stream with a few line buffers, at full 4K frame rate, with a few watts and
fixed latency. The on-chip DPU could run the crop classifier; tracker, threat logic and callouts
stay on the ARM cores (the existing Python code).

Not now: FPGA development (Vivado/Vitis HLS) takes weeks per change, and stage 1 will still change
with the first real recordings. The plan is to stabilise the algorithms on PC/Jetson first and
then evaluate an FPGA for a production version: lower power and heat, and the usual path for
airborne hardware assurance (DO-254). Stage 1 is kept FPGA-friendly: fixed kernel size, integer
arithmetic, local operations only.

## Power supply (14 V aircraft bus)

* The bus is really 12–14.5 V, briefly below 9 V while cranking, with spikes when large loads switch.
* **Always use a DC/DC converter** with a wide input range (9–36 V), isolated, with an EMC filter.
  It provides a stable 12 V or 19 V and also suits 28 V aircraft.
* Total power ~40–60 W → 3–4.5 A at 14 V.
* Connecting via a sufficiently fused cabin power socket keeps the device portable. A fixed
  connection to the bus is a modification of the aircraft.
* For the first test flights: a LiFePO4 battery (12 V) – completely independent of the aircraft bus.

## EMC

Before the first flight, check on the ground with the engine running:

* COM: noise/whistling on several frequencies, squelch opening?
* NAV/GPS: signal loss, flags?
* Compass: deviation caused by the enclosure and cables?

Typical sources: switching regulators of the Jetson and the converter, GMSL cables, USB 3.
Remedies: ferrites, short cables, distance from antenna cables, metal enclosure.
