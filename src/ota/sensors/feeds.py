"""Line-oriented data feeds over TCP or a serial port, with automatic reconnect.

Used for every traffic source of the recorder:

=================  ======================  ===============================
source             address example         protocol
=================  ======================  ===============================
ADS-B (dump1090)   ``127.0.0.1:30003``     SBS-1 / BaseStation CSV
OGN online         ``aprs.glidernet.org:14580``  APRS-IS (login + filter)
OGN local receiver ``192.168.1.50:50001``  APRS lines from ogn-decode
FLARM device       ``COM4@19200``          NMEA ($PFLAA, $GPRMC, ...)
FLARM via Wi-Fi    ``10.10.10.10:2000``    NMEA over TCP
=================  ======================  ===============================

Address syntax: ``host:port`` (or ``tcp:host:port``) for TCP, ``COM4`` /
``COM4@19200`` / ``/dev/ttyUSB0@38400`` for serial ports (needs pyserial).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


def is_serial(address: str) -> bool:
    a = address.upper()
    return a.startswith("COM") or address.startswith("/dev/")


class LineFeed:
    """Reads text lines from ``address`` in a background thread and calls ``on_line(line, rx_time)``.

    ``login`` is sent after every (re)connect, ``keepalive`` every ``keepalive_s``
    seconds (APRS-IS servers drop silent clients)."""

    def __init__(self, name: str, address: str, on_line: Callable[[str, float], None],
                 login: str | None = None, keepalive: str | None = None,
                 keepalive_s: float = 240.0, retry_s: float = 5.0):
        self.name = name
        self.address = address.strip()
        self.on_line = on_line
        self.login = login
        self.keepalive = keepalive
        self.keepalive_s = keepalive_s
        self.retry_s = retry_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn = None
        # status, read by the GUI
        self.connected = False
        self.lines = 0
        self.last_rx = 0.0
        self.error = ""

    def start(self) -> "LineFeed":
        self._thread = threading.Thread(target=self._run, name=f"feed-{self.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        c = self._conn
        if c is not None:
            try:
                c.close()
            except Exception:  # pragma: no cover
                pass
        if self._thread:
            self._thread.join(timeout=3)

    # --- transports -----------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if is_serial(self.address):
                    self._run_serial()
                else:
                    self._run_tcp()
            except Exception as e:  # network errors, missing ports, ...
                self.error = str(e) or e.__class__.__name__
                if not self._stop.is_set():
                    log.warning("%s: %s - retrying in %.0fs", self.name, self.error, self.retry_s)
            self.connected = False
            self._stop.wait(self.retry_s)

    def _emit(self, raw: bytes) -> None:
        line = raw.decode("ascii", errors="replace").strip()
        if not line:
            return
        now = time.time()
        self.lines += 1
        self.last_rx = now
        try:
            self.on_line(line, now)
        except Exception:  # a bad line must never kill the feed
            log.exception("%s: error handling line %r", self.name, line[:120])

    def _run_tcp(self) -> None:
        addr = self.address[4:] if self.address.lower().startswith("tcp:") else self.address
        host, _, port = addr.rpartition(":")
        with socket.create_connection((host, int(port)), timeout=10) as s:
            self._conn = s
            s.settimeout(1.0)
            self.connected, self.error = True, ""
            if self.login:
                s.sendall((self.login.rstrip("\r\n") + "\r\n").encode("ascii"))
            buf = b""
            last_ka = time.monotonic()
            while not self._stop.is_set():
                if self.keepalive and time.monotonic() - last_ka > self.keepalive_s:
                    s.sendall((self.keepalive + "\r\n").encode("ascii"))
                    last_ka = time.monotonic()
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("connection closed by peer")
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    self._emit(raw)

    def _run_serial(self) -> None:
        try:
            import serial  # pip install pyserial
        except ImportError as e:
            raise RuntimeError("pyserial is not installed (pip install pyserial)") from e
        port, _, baud = self.address.partition("@")
        with serial.Serial(port, int(baud or 19200), timeout=1.0) as s:
            self._conn = s
            self.connected, self.error = True, ""
            while not self._stop.is_set():
                raw = s.readline()
                if raw:
                    self._emit(raw)
