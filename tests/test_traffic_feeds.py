import datetime as dt
import math

import pytest

from ota.sensors.adsb import SbsParser, sbs_report
from ota.sensors.flarm import FlarmNmeaParser, checksum_ok
from ota.sensors.ogn import login_line, parse_aprs
from ota.sensors.traffic import (TrafficReport, TrafficTable, look_angles, offset_position,
                                 pressure_to_qnh_altitude)

RX = dt.datetime(2026, 10, 20, 7, 45, 50, tzinfo=dt.timezone.utc).timestamp()


def nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def test_ogn_aircraft_beacon():
    line = ("FLRDDA5BA>OGFLR,qAS,LIPB:/074548h4627.61N/01119.55E'086/078/A=004921 !W26! "
            "id06DDA5BA -019fpm +0.0rot 9.5dB 0e -1.2kHz gps2x3")
    r = parse_aprs(line, RX)
    assert r is not None
    assert r.src == "ogn" and r.addr == "DDA5BA" and r.addr_type == "flarm"
    assert r.acft_type == "glider"
    assert r.lat == pytest.approx(46 + 27.612 / 60, abs=1e-7)
    assert r.lon == pytest.approx(11 + 19.556 / 60, abs=1e-7)
    assert r.alt_m == pytest.approx(4921 * 0.3048)
    assert r.track_deg == 86 and r.gs_kt == 78 and r.vs_fpm == -19
    assert r.rx - r.t == pytest.approx(2.0)          # position time from the beacon
    assert r.key == "flarm:DDA5BA"


def test_ogn_ignores_receivers_comments_and_notrack():
    assert parse_aprs("# aprsc 2.1.14 20 Oct 2026 07:45:50 GMT GLIDERN1", RX) is None
    receiver = "LIPB>OGNSDR,TCPIP*,qAC,GLIDERN2:/074548h4627.61NI01119.55E&/A=000794"
    assert parse_aprs(receiver, RX) is None
    notrack = ("FLRDDA5BA>OGFLR,qAS,LIPB:/074548h4627.61N/01119.55E'086/078/A=004921 "
               "id46DDA5BA")  # 0x40 = no-tracking flag
    assert parse_aprs(notrack, RX) is None


def test_ogn_midnight_wrap():
    rx = dt.datetime(2026, 10, 21, 0, 0, 3, tzinfo=dt.timezone.utc).timestamp()
    r = parse_aprs("ICA3C6444>OGADSB,qAS,X:/235958h4627.61N/01119.55E^180/120/A=010000 "
                   "id253C6444", rx)
    assert r.addr_type == "icao" and r.acft_type == "jet"
    assert rx - r.t == pytest.approx(5.0)


def test_ogn_login():
    s = login_line(46.4603, 11.3264, 50)
    assert "pass -1" in s and s.endswith("filter r/46.4603/11.3264/50")


def test_sbs_report_only_for_positions():
    p = SbsParser()
    assert sbs_report(p, "MSG,1,1,1,4CA2D6,1,,,,,RYR12AB ,,,,,,,,,,,", RX) is None
    assert sbs_report(p, "MSG,4,1,1,4CA2D6,1,,,,,,,250,185,,,-640,,,,,", RX) is None
    r = sbs_report(p, "MSG,3,1,1,4CA2D6,1,,,,,,4500,,,46.4602,11.3264,,,0,0,0,0", RX)
    assert r.callsign == "RYR12AB" and r.alt_ref == "baro"
    assert r.alt_m == pytest.approx(4500 * 0.3048)
    assert r.gs_kt == 250 and r.track_deg == 185 and r.vs_fpm == -640
    assert r.key == "icao:4CA2D6"


def test_flarm_nmea_relative_to_absolute():
    p = FlarmNmeaParser()
    assert checksum_ok(nmea("GPGGA,074550,4627.618,N,01119.570,E,1,8,1.0,240.0,M,47.0,M,,"))
    assert not checksum_ok("$GPGGA,1,2*00")
    p.feed(nmea("GPGGA,074550,4627.618,N,01119.570,E,1,8,1.0,240.0,M,47.0,M,,"), RX)
    own = p.feed(nmea("GPRMC,074550,A,4627.618,N,01119.570,E,0.0,0.0,201026,,,A"), RX)
    assert own[0].src == "own" and own[0].alt_m == 240.0
    assert own[0].t == pytest.approx(RX)
    out = p.feed(nmea("PFLAA,0,1000,-500,300,2,DD1234,90,0,30,1.5,1"), RX)
    r = out[0]
    assert r.src == "flarm" and r.addr == "DD1234" and r.acft_type == "glider"
    assert r.alt_m == 540.0
    lat0, lon0 = own[0].lat, own[0].lon
    az, el, rng = look_angles(lat0, lon0, 240.0, r.lat, r.lon, r.alt_m)
    assert rng == pytest.approx(math.sqrt(1000**2 + 500**2 + 300**2), rel=2e-3)
    assert az == pytest.approx((math.degrees(math.atan2(-500, 1000)) + 360) % 360, abs=0.1)
    # Mode-S target without bearing is skipped
    assert p.feed(nmea("PFLAA,0,1500,,100,1,4CA2D6,,,,,9"), RX) == []


def test_geodesy_and_qnh():
    lat, lon = offset_position(46.0, 11.0, 3000, 4000)
    az, el, rng = look_angles(46.0, 11.0, 0.0, lat, lon, 0.0)
    assert rng == pytest.approx(5000, rel=2e-3)
    assert el < 0  # earth curvature: a point at the same height is slightly below the horizontal
    assert pressure_to_qnh_altitude(1000.0, 1013.25) == pytest.approx(1000.0)
    assert pressure_to_qnh_altitude(1000.0, 1023.25) == pytest.approx(1000 + 83, abs=3)


def test_traffic_table_keeps_callsign():
    tab = TrafficTable()
    tab.add(TrafficReport(src="adsb", addr="AA", callsign="DLH1", lat=1, lon=1, rx=100))
    tab.add(TrafficReport(src="ogn", addr="AA", lat=1, lon=1, rx=101))
    act = tab.active(now=110)
    assert len(act) == 1 and act[0].callsign == "DLH1" and act[0].src == "ogn"
    rt = TrafficReport.from_dict(__import__("json").loads(act[0].to_json()))
    assert rt.key == "icao:AA"


def test_line_feed_tcp_login_and_reconnect():
    import socket
    import threading
    import time
    from ota.sensors.feeds import LineFeed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    got_login = []

    def serve():
        for _ in range(2):  # second connection proves the reconnect
            c, _ = srv.accept()
            got_login.append(c.recv(200).decode())
            c.sendall(b"# server banner\r\nline A\r\npartial ")
            time.sleep(0.05)
            c.sendall(b"line B\r\n")
            c.close()

    threading.Thread(target=serve, daemon=True).start()
    lines = []
    feed = LineFeed("test", f"127.0.0.1:{port}", lambda l, t: lines.append(l),
                    login="user X pass -1", retry_s=0.1).start()
    deadline = time.time() + 5
    while len(lines) < 6 and time.time() < deadline:
        time.sleep(0.02)
    feed.stop()
    srv.close()
    assert lines[:3] == ["# server banner", "line A", "partial line B"]
    assert len(lines) == 6 and feed.lines == 6
    assert got_login[0].strip() == "user X pass -1"
