# Data collection

Own recordings are the most valuable material: they show exactly the camera, windscreen, light and
landscape the system will have to work with.

## On the ground

**Location:** apron or edge of an airfield with traffic (e.g. looking towards final approach), with
the aerodrome operator's permission. Arriving traffic is ideal test material: a target coming
straight at the camera and growing – exactly the collision geometry.

```powershell
ota record --camera usb:0 --width 3840 --height 2160 --fps 15 --out data\raw\lipb_2026-10-20_rwy19 --seconds 600
```

`ota record` writes near-lossless MJPEG (`.avi`) plus a `.timestamps.csv` with the real frame
times. 4K at 15 fps is roughly 1–2 GB per minute – use a fast SSD.

For every recording note (e.g. in `data/raw/README.md`, not in the repository):

* date, time, location, viewing direction
* lens / field of view
* weather, sun position
* known movements (time, registration) – recording ADS-B in parallel is ideal

## In flight (phase 1: laptop as recorder)

* Fix the camera behind the windscreen, with a sun shade.
* Stow the laptop securely; nothing may come loose in turbulence.
* Record only, no warnings – evaluation happens on the ground.
* Record ADS-B in parallel (dump1090 logs) for ground truth.
* Operation must not interfere with flying: start recording before engine start, stop after shutdown.

## Evaluation

```powershell
ota run --video data\raw\flight1.avi --hfov 50 --record out\flight1_overlay.mp4
```

* Watch `out\flight1_overlay.mp4`: were the known aircraft found? When?
* Compare `out\events.jsonl` with ADS-B data: are clock positions right, how good is τ?
* Save false candidates (reflections, cloud edges) as `background` crops for training.

## Privacy

Recordings may show people, registrations and private property. Raw videos stay local (`data/` is in
`.gitignore`); at most selected excerpts are published.
