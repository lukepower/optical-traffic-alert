"""ADS-B traffic from dump1090 / readsb via the SBS-1 (BaseStation) text feed.

Setup (Windows or Linux): RTL-SDR stick + dump1090/readsb, which serves
CSV lines on TCP port 30003, e.g.::

    MSG,3,1,1,4CA2D6,1,2026/10/18,09:20:01.000,2026/10/18,09:20:01.000,,4500,,,46.4602,11.3264,,,0,0,0,0

Why fuse ADS-B with the cameras?

* An optical track that matches an ADS-B target is *cooperative* traffic -
  the pilot probably already has it on the EFB/FLARM display. The optical
  system's real value is the *non-cooperative* traffic: gliders without FLARM,
  paragliders, ultralights, drones.
* ADS-B targets give ground truth (distance, altitude) to validate and tune
  the optical tau estimate during test flights.

FLARM on 868 MHz comes from OGN (``ogn.py``) or a FLARM device's data port
(``flarm.py``). For recording, every source is normalised to ``TrafficReport``
(``traffic.py``); ``sbs_report`` does that for SBS lines.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass

from ..geometry import bearing_and_distance, wrap180
from .traffic import FT, TrafficReport

log = logging.getLogger(__name__)


@dataclass
class TrafficTarget:
    icao: str
    callsign: str = ""
    lat: float | None = None
    lon: float | None = None
    alt_ft: float | None = None
    track_deg: float | None = None
    speed_kt: float | None = None
    last_seen: float = 0.0
    source: str = "adsb"
    vs_fpm: float | None = None


class SbsParser:
    """Stateful parser for SBS-1 lines. Keeps the latest state per ICAO address."""

    def __init__(self, clock=time.monotonic):
        self.targets: dict[str, TrafficTarget] = {}
        self.clock = clock

    def feed(self, line: str) -> TrafficTarget | None:
        p = line.strip().split(",")
        if len(p) < 22 or p[0] != "MSG":
            return None
        icao = p[4].upper()
        if not icao:
            return None
        tgt = self.targets.setdefault(icao, TrafficTarget(icao=icao))

        def num(s: str) -> float | None:
            try:
                return float(s) if s else None
            except ValueError:
                return None

        if p[10].strip():
            tgt.callsign = p[10].strip()
        for attr, idx in (("alt_ft", 11), ("speed_kt", 12), ("track_deg", 13),
                          ("lat", 14), ("lon", 15), ("vs_fpm", 16)):
            v = num(p[idx])
            if v is not None:
                setattr(tgt, attr, v)
        tgt.last_seen = self.clock()
        return tgt

    def active(self, max_age_s: float) -> list[TrafficTarget]:
        now = self.clock()
        return [t for t in self.targets.values()
                if now - t.last_seen <= max_age_s and t.lat is not None]


def sbs_report(parser: SbsParser, line: str, rx: float) -> TrafficReport | None:
    """Feed one SBS line; return a ``TrafficReport`` if it carried a new airborne position.

    SBS altitudes are pressure altitudes (1013.25 hPa) -> ``alt_ref='baro'``."""
    tgt = parser.feed(line)
    if tgt is None or line.split(",")[1:2] != ["3"] or tgt.lat is None:
        return None
    return TrafficReport(
        src="adsb", addr=tgt.icao, addr_type="icao", t=rx, rx=rx, lat=tgt.lat, lon=tgt.lon,
        alt_m=tgt.alt_ft * FT if tgt.alt_ft is not None else None, alt_ref="baro",
        track_deg=tgt.track_deg, gs_kt=tgt.speed_kt, vs_fpm=tgt.vs_fpm, callsign=tgt.callsign)


class SbsClient:
    """Background TCP reader that keeps an ``SbsParser`` up to date."""

    def __init__(self, host: str, port: int, parser: SbsParser | None = None):
        self.host, self.port = host, port
        self.parser = parser or SbsParser()
        self._stop = threading.Event()

    def start(self) -> "SbsClient":
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:  # pragma: no cover - network
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as s:
                    f = s.makefile("r", encoding="ascii", errors="ignore")
                    for line in f:
                        if self._stop.is_set():
                            return
                        self.parser.feed(line)
            except OSError as e:
                log.warning("ADS-B feed %s:%s unavailable (%s), retrying", self.host, self.port, e)
                time.sleep(3)


def relative_position(own_lat: float, own_lon: float, own_alt_ft: float, own_heading_deg: float,
                      tgt: TrafficTarget) -> tuple[float, float, float]:
    """Return (relative azimuth deg, distance m, altitude difference ft) of a target."""
    brg, dist = bearing_and_distance(own_lat, own_lon, tgt.lat, tgt.lon)
    rel_az = wrap180(brg - own_heading_deg)
    dalt = (tgt.alt_ft - own_alt_ft) if tgt.alt_ft is not None else float("nan")
    return rel_az, dist, dalt
