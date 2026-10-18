"""WitMotion IMU / AHRS (WT901C, HWT901B, WT61, ...) over USB serial.

The modules stream 11-byte binary packets::

    0x55  type  d0 d1 d2 d3 d4 d5 d6 d7  checksum      (checksum = sum of the first 10 bytes & 0xFF)

    type 0x51 acceleration   ax ay az (int16 LE) / 32768 * 16      [g]
    type 0x52 angular rate   wx wy wz (int16 LE) / 32768 * 2000    [deg/s]
    type 0x53 angle          roll pitch yaw (int16 LE) / 32768 * 180 [deg]
    type 0x59 quaternion     q0 q1 q2 q3 (int16 LE) / 32768

Axes (module lying flat, label up): X forward, Y left, Z up. The accelerometer
measures *specific force*, i.e. +1 g on Z when the module is at rest.

The recorder stores one CSV row per complete sample (on every angle packet,
with the latest acceleration and rate) in ``imu.csv``. The device's own
roll/pitch are logged too, but the evaluation computes its own attitude from
the raw gyro + accelerometer with GPS aiding (``attitude.py``), because a
gravity-referenced AHRS is fooled by the centripetal acceleration in a
coordinated turn.

Set the module to 100-200 Hz and 115200 baud with the WitMotion PC software
once; the settings are stored in the module.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER = 0x55
ACC, GYRO, ANGLE, QUAT = 0x51, 0x52, 0x53, 0x59


@dataclass
class ImuSample:
    t: float
    ax: float = 0.0   # g, specific force
    ay: float = 0.0
    az: float = 0.0
    gx: float = 0.0   # deg/s
    gy: float = 0.0
    gz: float = 0.0
    roll: float = 0.0   # deg, device's own estimate (logged only)
    pitch: float = 0.0
    yaw: float = 0.0

    CSV_HEADER = "t_wall,ax,ay,az,gx,gy,gz,roll,pitch,yaw"

    def to_csv(self) -> str:
        return (f"{self.t:.4f},{self.ax:.5f},{self.ay:.5f},{self.az:.5f},{self.gx:.4f},"
                f"{self.gy:.4f},{self.gz:.4f},{self.roll:.3f},{self.pitch:.3f},{self.yaw:.3f}")


class WitMotionParser:
    """Incremental parser: feed raw bytes, get complete samples."""

    def __init__(self):
        self.buf = bytearray()
        self.cur = ImuSample(0.0)
        self.bad = 0
        self.packets = 0

    def feed(self, data: bytes, rx: float) -> list[ImuSample]:
        self.buf += data
        out = []
        b = self.buf
        i = 0
        while len(b) - i >= 11:
            if b[i] != HEADER:
                i += 1
                continue
            pkt = b[i:i + 11]
            if sum(pkt[:10]) & 0xFF != pkt[10]:
                self.bad += 1
                i += 1
                continue
            kind = pkt[1]
            v = struct.unpack_from("<hhhh", pkt, 2)
            c = self.cur
            if kind == ACC:
                c.ax, c.ay, c.az = (x / 32768.0 * 16.0 for x in v[:3])
            elif kind == GYRO:
                c.gx, c.gy, c.gz = (x / 32768.0 * 2000.0 for x in v[:3])
            elif kind == ANGLE:
                c.roll, c.pitch, c.yaw = (x / 32768.0 * 180.0 for x in v[:3])
                c.t = rx
                out.append(ImuSample(**{k: getattr(c, k) for k in c.__dataclass_fields__}))
            self.packets += 1
            i += 11
        del b[:i]
        return out


def encode(kind: int, values: tuple[float, float, float], scale: float) -> bytes:
    """Build one packet (used by the synthetic data generator and tests)."""
    ints = [max(-32768, min(32767, int(round(v / scale * 32768.0)))) for v in values] + [0]
    body = bytes([HEADER, kind]) + struct.pack("<hhhh", *ints)
    return body + bytes([sum(body) & 0xFF])


def encode_sample(s: ImuSample) -> bytes:
    return (encode(ACC, (s.ax, s.ay, s.az), 16.0) + encode(GYRO, (s.gx, s.gy, s.gz), 2000.0)
            + encode(ANGLE, (s.roll, s.pitch, s.yaw), 180.0))
