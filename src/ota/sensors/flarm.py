"""Traffic from a FLARM device (PowerFLARM, FLARM Mouse, SoftRF, AT-1 ...) via its NMEA data port.

Many club aircraft already carry a FLARM. Its data port (serial, or TCP over
Wi-Fi on some devices) outputs standard NMEA plus the FLARM sentences:

* ``$GPRMC`` / ``$GPGGA`` - own position, ground speed, track, GNSS altitude
* ``$PFLAA`` - one line per received aircraft, position *relative* to own:
  ``$PFLAA,<alarm>,<relN m>,<relE m>,<relV m>,<idtype>,<id>,<track>,<turn>,<gs m/s>,<climb m/s>,<type>``

``$PFLAA`` targets are converted to absolute positions with the latest own fix.
FLARM also reports Mode-S/ADS-B aircraft; Mode-S targets without a position
have an empty relative-east field and are skipped.

Reference: FLARM "Data Port Specification" (ICD), freely available from flarm.com.
"""

from __future__ import annotations

import datetime as dt

from .traffic import ADDRESS_TYPES, AIRCRAFT_TYPES, TrafficReport, offset_position

KT = 1.0 / 0.514444


def checksum_ok(line: str) -> bool:
    if "*" not in line:
        return True  # some devices omit it
    body, _, cs = line.lstrip("$").partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cs[:2], 16)
    except ValueError:
        return False


def _f(s: str) -> float | None:
    try:
        return float(s) if s.strip() else None
    except ValueError:
        return None


def _latlon(v: str, hemi: str, deg_digits: int) -> float | None:
    if not v:
        return None
    x = float(v[:deg_digits]) + float(v[deg_digits:]) / 60.0
    return -x if hemi in ("S", "W") else x


class FlarmNmeaParser:
    def __init__(self):
        self.own_lat: float | None = None
        self.own_lon: float | None = None
        self.own_alt_m: float | None = None
        self.own_track: float | None = None
        self.own_gs_kt: float | None = None
        self.own_t: float = 0.0

    def feed(self, line: str, rx: float) -> list[TrafficReport]:
        line = line.strip()
        if not line.startswith("$") or not checksum_ok(line):
            return []
        f = line.split("*")[0].split(",")
        kind = f[0][3:] if f[0][1:3] in ("GP", "GN", "GL", "GA") else f[0][1:]
        if kind == "RMC" and len(f) >= 10:
            if f[2] != "A":
                return []
            self.own_lat = _latlon(f[3], f[4], 2)
            self.own_lon = _latlon(f[5], f[6], 3)
            self.own_gs_kt = _f(f[7])
            self.own_track = _f(f[8])
            self.own_t = self._fix_time(f[1], f[9], rx)
            if self.own_lat is None:
                return []
            return [TrafficReport(src="own", addr="OWN", addr_type="own", t=self.own_t, rx=rx,
                                  lat=self.own_lat, lon=self.own_lon, alt_m=self.own_alt_m,
                                  alt_ref="gps", track_deg=self.own_track, gs_kt=self.own_gs_kt)]
        if kind == "GGA" and len(f) >= 10:
            if f[6] not in ("", "0"):
                self.own_alt_m = _f(f[9])
            return []
        if kind == "PFLAA" and len(f) >= 11:
            return self._pflaa(f, rx)
        return []

    def _fix_time(self, hhmmss: str, ddmmyy: str, rx: float) -> float:
        try:
            d = dt.datetime.strptime(ddmmyy + hhmmss[:6], "%d%m%y%H%M%S").replace(
                tzinfo=dt.timezone.utc)
            t = d.timestamp() + (float(hhmmss[6:]) if len(hhmmss) > 6 else 0.0)
            return t if abs(t - rx) < 3600 else rx
        except ValueError:
            return rx

    def _pflaa(self, f: list[str], rx: float) -> list[TrafficReport]:
        n, e, v = _f(f[2]), _f(f[3]), _f(f[4])
        if n is None or e is None or self.own_lat is None:
            return []
        lat, lon = offset_position(self.own_lat, self.own_lon, n, e)
        id_type = int(f[5]) if f[5].isdigit() else 0
        acft_code = int(f[11], 16) if len(f) > 11 and f[11] else 0
        gs = _f(f[9])
        climb = _f(f[10])
        alt = (self.own_alt_m + v) if (self.own_alt_m is not None and v is not None) else None
        return [TrafficReport(
            src="flarm", addr=f[6].upper(), addr_type=ADDRESS_TYPES.get(id_type, "random"),
            t=rx, rx=rx, lat=lat, lon=lon, alt_m=alt, alt_ref="gps",
            track_deg=_f(f[7]), gs_kt=gs * KT if gs is not None else None,
            vs_fpm=climb * 196.85 if climb is not None else None,
            acft_type=AIRCRAFT_TYPES.get(acft_code, "unknown"),
            extra={"alarm": int(f[1]) if f[1].isdigit() else 0, "rel_n": n, "rel_e": e, "rel_v": v})]
