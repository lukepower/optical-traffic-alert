"""Own-aircraft attitude and rotation rates.

The BNO085 is read by a small USB microcontroller (RP2040/ESP32) that prints
one line per sample over the serial port::

    IMU,<yaw_rate_dps>,<pitch_rate_dps>,<roll_rate_dps>,<roll_deg>,<pitch_deg>

The rates are used to remove own rotation from the bearing rate of tracks
(see tracking/threat.py). Roll/pitch angles will later rotate "high/low"
from body axes to the horizon.

Without hardware, ``NullImu`` returns zeros - fine for ground tests with a
fixed camera.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ImuSample:
    yaw_rate_dps: float = 0.0
    pitch_rate_dps: float = 0.0
    roll_rate_dps: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0


def parse_imu_line(line: str) -> ImuSample | None:
    p = line.strip().split(",")
    if len(p) != 6 or p[0] != "IMU":
        return None
    try:
        return ImuSample(*map(float, p[1:]))
    except ValueError:
        return None


class NullImu:
    def latest(self) -> ImuSample:
        return ImuSample()


class SerialImu:  # pragma: no cover - needs hardware
    def __init__(self, port: str, baud: int = 115200):
        import serial  # pip install -e .[serial]
        self.ser = serial.Serial(port, baud, timeout=1)
        self._latest = ImuSample()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            raw = self.ser.readline().decode("ascii", errors="ignore")
            s = parse_imu_line(raw)
            if s:
                self._latest = s

    def latest(self) -> ImuSample:
        return self._latest
