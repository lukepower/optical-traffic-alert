"""Common traffic record for all cooperative sources (ADS-B, OGN, FLARM).

Every position report from any source is normalised into a ``TrafficReport``
and written as one JSON line to ``traffic.jsonl`` by the session recorder.
The evaluation tool (``ota evaluate``) only reads this format, so it does
not care whether a position came from dump1090, the OGN network or the
aircraft's own FLARM.

Times are UNIX epoch seconds (UTC) from the recording computer's clock:

* ``t``  - time the position is valid for (from the message if it carries
  one, e.g. the OGN ``hhmmss`` field, otherwise the receive time)
* ``rx`` - time the message was received

Identity: ``key`` = ``<addr_type>:<addr>``. ICAO addresses are shared between
ADS-B and OGN (which also relays ADS-B), so the same aircraft seen by both
sources ends up under one key.

Altitudes are metres. ``alt_ref`` says what they mean:

* ``baro`` - pressure altitude (1013.25 hPa), as transmitted by ADS-B/Mode S
* ``gps``  - GNSS altitude above mean sea level (OGN/FLARM)
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import asdict, dataclass, field

FT = 0.3048

# FLARM / OGN aircraft type codes
AIRCRAFT_TYPES = {
    0: "unknown", 1: "glider", 2: "tow plane", 3: "helicopter", 4: "skydiver",
    5: "drop plane", 6: "hang glider", 7: "paraglider", 8: "powered aircraft",
    9: "jet", 10: "unknown", 11: "balloon", 12: "airship", 13: "drone", 14: "unknown",
    15: "static obstacle",
}
ADDRESS_TYPES = {0: "random", 1: "icao", 2: "flarm", 3: "ogn"}


@dataclass
class TrafficReport:
    src: str                      # adsb | ogn | flarm | own (ownship)
    addr: str                     # hex address, upper case
    addr_type: str = "icao"       # icao | flarm | ogn | random
    t: float = 0.0                # position time, epoch s
    rx: float = 0.0               # receive time, epoch s
    lat: float | None = None
    lon: float | None = None
    alt_m: float | None = None
    alt_ref: str = "gps"          # gps | baro
    track_deg: float | None = None
    gs_kt: float | None = None
    vs_fpm: float | None = None
    callsign: str = ""
    acft_type: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.addr_type}:{self.addr}"

    def to_json(self) -> str:
        d = {k: v for k, v in asdict(self).items() if v not in (None, "", {})}
        for k in ("lat", "lon"):
            if k in d:
                d[k] = round(d[k], 6)
        for k in ("t", "rx"):
            d[k] = round(d[k], 3)
        for k in ("alt_m", "track_deg", "gs_kt", "vs_fpm"):
            if k in d:
                d[k] = round(d[k], 1)
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict) -> "TrafficReport":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def pressure_to_qnh_altitude(alt_baro_m: float, qnh_hpa: float) -> float:
    """Pressure altitude -> altitude above MSL for a given QNH (ICAO lower atmosphere).

    About +8.3 m (27 ft) per hPa above standard pressure."""
    p = 1013.25 * (1 - alt_baro_m / 44330.8) ** 5.25588
    return 44330.8 * (1 - (p / qnh_hpa) ** 0.190263)


# --- geodesy ----------------------------------------------------------------------------------

_A = 6378137.0
_E2 = 6.69437999014e-3


def to_ecef(lat: float, lon: float, alt: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(la) ** 2)
    return ((n + alt) * math.cos(la) * math.cos(lo),
            (n + alt) * math.cos(la) * math.sin(lo),
            (n * (1 - _E2) + alt) * math.sin(la))


def enu(lat0: float, lon0: float, alt0: float, lat: float, lon: float, alt: float
        ) -> tuple[float, float, float]:
    """East/North/Up (m) of a point relative to an observer (WGS84, exact)."""
    x0, y0, z0 = to_ecef(lat0, lon0, alt0)
    x, y, z = to_ecef(lat, lon, alt)
    dx, dy, dz = x - x0, y - y0, z - z0
    la, lo = math.radians(lat0), math.radians(lon0)
    e = -math.sin(lo) * dx + math.cos(lo) * dy
    n = (-math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy
         + math.cos(la) * dz)
    u = (math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy
         + math.sin(la) * dz)
    return e, n, u


def look_angles(lat0: float, lon0: float, alt0: float, lat: float, lon: float, alt: float
                ) -> tuple[float, float, float]:
    """True azimuth (deg), elevation above the local horizontal (deg) and slant range (m)."""
    e, n, u = enu(lat0, lon0, alt0, lat, lon, alt)
    az = (math.degrees(math.atan2(e, n)) + 360.0) % 360.0
    el = math.degrees(math.atan2(u, math.hypot(e, n)))
    return az, el, math.sqrt(e * e + n * n + u * u)


def offset_position(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Small-offset position (flat earth, fine for FLARM's few km)."""
    r = 6371000.0
    return (lat + math.degrees(north_m / r),
            lon + math.degrees(east_m / (r * math.cos(math.radians(lat)))))


# --- live table ---------------------------------------------------------------------------------

class TrafficTable:
    """Thread-safe 'latest report per aircraft' table, for the recorder's live display."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: dict[str, TrafficReport] = {}
        self.counts: dict[str, int] = {}

    def add(self, r: TrafficReport) -> None:
        with self._lock:
            self.counts[r.src] = self.counts.get(r.src, 0) + 1
            if r.src == "own":
                self._latest["own"] = r
                return
            old = self._latest.get(r.key)
            if old and not r.callsign:
                r.callsign = old.callsign
            self._latest[r.key] = r

    def ownship(self) -> TrafficReport | None:
        with self._lock:
            return self._latest.get("own")

    def active(self, max_age_s: float = 60.0, now: float | None = None) -> list[TrafficReport]:
        now = time.time() if now is None else now
        with self._lock:
            return [r for k, r in self._latest.items()
                    if k != "own" and r.lat is not None and now - r.rx <= max_age_s]
