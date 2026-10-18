# Hardware

The full bill of materials with prices (October 2026, EUR) is in
[hardware/bill-of-materials.xlsx](hardware/bill-of-materials.xlsx) (spreadsheet in German).

## Phase 1 – laptop (ground tests)

| Component | Example | approx. price |
|---|---|---|
| Laptop | existing (Intel Iris Xe is enough) | – |
| USB camera | **in use:** Svpro 4K USB 3.0 camera, Sony IMX415, YUYV, CS-mount 5–50 mm zoom (Amazon B0CZDHVYLM) | €193 |
| SDR for ADS-B | **in use:** Nooelec NESDR SMArt v5 (RTL2832U + R820T2, 0.5 ppm TCXO, 3 antennas); RTL-SDR Blog V4 is equivalent | €58 |
| Second SDR for FLARM/OGN (868 MHz) | later | ~€50 |
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
