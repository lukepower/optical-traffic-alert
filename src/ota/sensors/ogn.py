"""FLARM and other 868 MHz traffic via the Open Glider Network (OGN).

FLARM transmissions are decoded by OGN receivers and published in APRS
format. The recorder can get them two ways - both deliver the same lines,
so one parser serves both:

1. **Online** from the OGN APRS servers (``aprs.glidernet.org:14580``) with a
   range filter around the station. Needs internet (a phone hotspot is fine).
   Only aircraft that some OGN ground station receives are included; low
   traffic in valleys may be missing.
2. **Local receiver**: a second RTL-SDR stick on 868 MHz with the OGN receiver
   software (``ogn-rf`` + ``ogn-decode``, Linux - e.g. a Raspberry Pi). Its
   ``ogn-decode`` serves the same APRS lines on TCP port 50001.

Example aircraft beacon::

    FLRDDA5BA>OGFLR,qAS,LIPB:/074548h4627.61N/01119.55E'086/078/A=004921 !W26! id06DDA5BA -019fpm +0.0rot 9.5dB 0e -1.2kHz gps2x3

``/074548h`` is the UTC time of the position, ``!W26!`` adds a third decimal
to the latitude/longitude minutes, ``id06DDA5BA`` carries flags, aircraft type
and address. Aircraft with the *no-tracking* flag are dropped, as the OGN
data policy requires.
"""

from __future__ import annotations

import datetime as dt
import re

from .traffic import ADDRESS_TYPES, AIRCRAFT_TYPES, FT, TrafficReport

OGN_SERVER = "aprs.glidernet.org:14580"
OGN_LOCAL_PORT = 50001

_POS = re.compile(
    r"^(?P<call>[^>]+)>(?P<dest>[^,:]+)[^:]*:[/@]"
    r"(?P<time>\d{6})h"
    r"(?P<lat>\d{4}\.\d{2})(?P<ns>[NS])(?P<tab>.)"
    r"(?P<lon>\d{5}\.\d{2})(?P<ew>[EW])(?P<sym>.)"
    r"(?:(?P<crs>\d{3})/(?P<spd>\d{3}))?"
    r"/A=(?P<alt>-?\d{5,6})"
    r"(?P<rest>.*)$")
_ID = re.compile(r"\bid([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\b")
_DAO = re.compile(r"!W(\d)(\d)!")
_FPM = re.compile(r"([+-]\d+)fpm")


def login_line(lat: float, lon: float, radius_km: float, client: str = "OTA-REC") -> str:
    """Read-only APRS-IS login (passcode -1) with a range filter around the station."""
    return (f"user {client} pass -1 vers optical-traffic-alert 1.0 "
            f"filter r/{lat:.4f}/{lon:.4f}/{radius_km:.0f}")


def _position_time(hhmmss: str, rx: float) -> float:
    """Combine the beacon's UTC hhmmss with the receive date; handle midnight wrap."""
    rx_dt = dt.datetime.fromtimestamp(rx, tz=dt.timezone.utc)
    h, m, s = int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
    if h > 23 or m > 59 or s > 59:
        return rx
    t = rx_dt.replace(hour=h, minute=m, second=s, microsecond=0).timestamp()
    if t - rx > 12 * 3600:
        t -= 86400
    elif rx - t > 12 * 3600:
        t += 86400
    return t


def parse_aprs(line: str, rx: float) -> TrafficReport | None:
    """Parse one OGN APRS line. Returns None for receiver beacons, comments and
    aircraft that do not want to be tracked."""
    if not line or line.startswith("#"):
        return None
    m = _POS.match(line.strip())
    if not m:
        return None
    rest = m.group("rest")
    idm = _ID.search(rest)
    if not idm:
        return None  # receiver / station beacon, not an aircraft
    flags = int(idm.group(1), 16)
    if flags & 0x40:  # no-tracking flag
        return None
    addr_type = ADDRESS_TYPES.get(flags & 0x03, "random")
    acft = AIRCRAFT_TYPES.get((flags >> 2) & 0x0F, "unknown")
    addr = idm.group(2).upper()

    lat_s, lon_s = m.group("lat"), m.group("lon")
    lat_min, lon_min = float(lat_s[2:]), float(lon_s[3:])
    dao = _DAO.search(rest)
    if dao:
        lat_min += int(dao.group(1)) * 0.001
        lon_min += int(dao.group(2)) * 0.001
    lat = int(lat_s[:2]) + lat_min / 60.0
    lon = int(lon_s[:3]) + lon_min / 60.0
    if m.group("ns") == "S":
        lat = -lat
    if m.group("ew") == "W":
        lon = -lon

    vs = _FPM.search(rest)
    call = m.group("call")
    return TrafficReport(
        src="ogn", addr=addr, addr_type=addr_type,
        t=_position_time(m.group("time"), rx), rx=rx,
        lat=lat, lon=lon, alt_m=int(m.group("alt")) * FT, alt_ref="gps",
        track_deg=float(m.group("crs")) if m.group("crs") else None,
        gs_kt=float(m.group("spd")) if m.group("spd") else None,
        vs_fpm=float(vs.group(1)) if vs else None,
        acft_type=acft,
        extra={"call": call, "proto": call[:3]})
