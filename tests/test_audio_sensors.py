from ota.audio import Announcer, compose_phrase
from ota.audio.announcer import PrintSpeaker
from ota.config import AudioConfig
from ota.sensors.adsb import SbsParser, relative_position
from ota.sensors.imu import parse_imu_line
from ota.tracking.threat import Assessment, ThreatLevel


def _a(level, clock=2, vertical="high", track=1):
    return Assessment(track_id=track, camera="front", level=level, azimuth_deg=60,
                      elevation_deg=8, clock=clock, vertical=vertical, bearing_rate_dps=0.1,
                      growth_rate=0.1, tau_s=10, size_px=5)


def test_phrases_en_de():
    assert compose_phrase(_a(ThreatLevel.TRAFFIC)) == "Traffic, two o'clock, high."
    assert compose_phrase(_a(ThreatLevel.WARNING)) == "Traffic, traffic, two o'clock, high, closing."
    assert compose_phrase(_a(ThreatLevel.ADVISORY), "de") == "Verkehr, zwei Uhr, höher, nähert sich."


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_announcer_rate_limits_and_escalates():
    clock = _Clock()
    ann = Announcer(AudioConfig(min_gap_s=3, repeat_s=10), PrintSpeaker(), clock=clock, threaded=False)
    assert ann.offer([_a(ThreatLevel.ADVISORY)])
    clock.t = 1
    assert ann.offer([_a(ThreatLevel.ADVISORY)]) is None          # same track, too soon
    clock.t = 1.5
    assert "traffic, two" in ann.offer([_a(ThreatLevel.WARNING)])  # escalation goes through
    clock.t = 12
    assert ann.offer([_a(ThreatLevel.WARNING)])                   # repeat after repeat_s


def test_announcer_dedupes_split_tracks():
    clock = _Clock()
    ann = Announcer(AudioConfig(), PrintSpeaker(), clock=clock, threaded=False)
    assert ann.offer([_a(ThreatLevel.WARNING, track=1)])
    clock.t = 4
    # same sector, new track id (target briefly split) -> no second callout
    assert ann.offer([_a(ThreatLevel.WARNING, track=2)]) is None


def test_sbs_parser_and_relative_position():
    p = SbsParser(clock=lambda: 0.0)
    p.feed("MSG,1,1,1,4CA2D6,1,,,,,DLA123,,,,,,,,,,,0")
    p.feed("MSG,3,1,1,4CA2D6,1,,,,,,4500,,,46.5602,11.3264,,,0,0,0,0")
    [tgt] = p.active(15)
    assert tgt.callsign == "DLA123" and tgt.alt_ft == 4500
    rel_az, dist, dalt = relative_position(46.4602, 11.3264, 3500, 90.0, tgt)
    assert round(rel_az) == -90          # target due north, we fly east -> 9 o'clock
    assert 11000 < dist < 11300 and dalt == 1000


def test_imu_line():
    s = parse_imu_line("IMU,1.5,-0.2,0.0,10.0,2.0")
    assert s.yaw_rate_dps == 1.5 and s.roll_deg == 10.0
    assert parse_imu_line("garbage") is None
