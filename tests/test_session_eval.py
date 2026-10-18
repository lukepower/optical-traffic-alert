"""Session recorder and evaluation tool, end to end on synthetic data."""

import json
import socket
import threading
import time

import pytest

from ota.evaluate import SessionEvaluator
from ota.synth import make_synthetic_session


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return make_synthetic_session(tmp_path_factory.mktemp("sess"), duration_s=30.0, fps=8.0)


def test_evaluation_finds_aircraft_and_heading_error(session):
    ev = SessionEvaluator(session)
    s = ev.run()
    out = ev.write_outputs(s)
    # the heading/pitch error built into session.json is recovered
    assert s["camera_correction"]["applied"]
    assert s["camera_corrected"]["heading_deg"] == pytest.approx(190.0, abs=0.3)
    assert s["camera_corrected"]["pitch_deg"] == pytest.approx(3.0, abs=0.3)
    t = {x["key"]: x for x in s["targets"]}
    assert t["icao:4CA2D6"]["tracked_pct"] > 90
    assert t["icao:4CA2D6"]["max_track_km"] > 5
    assert t["flarm:DDA5BA"]["tracked_pct"] > 90
    assert t["flarm:DDA5BA"]["callsign"] == "" and t["icao:4CA2D6"]["callsign"] == "EIN5AB"
    assert t["icao:4CA2D6"]["median_error_deg"] < 0.3
    # the non-cooperative paraglider (az 200, ~8 deg) is reported as an unmatched track
    assert any(abs(u["az_deg"] - 200) < 1.5 and u["duration_s"] > 20 for u in s["unmatched_tracks"])
    assert s["ogn_latency_s"] == pytest.approx(1.5, abs=0.1)
    assert (out / "report.md").exists() and (out / "matches.csv").exists()
    assert list((out / "crops" / "aircraft").glob("*.png"))
    assert list((out / "crops" / "review").glob("*.png"))


def test_session_recorder_with_file_camera_and_adsb(session, tmp_path):
    from ota.recorder import SessionRecorder, SessionSettings

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        for i in range(30):
            c.sendall(f"MSG,3,1,1,4CA2D6,1,,,,,,4500,,,46.4{i:02d},11.3264,,,0,0,0,0\r\n".encode())
            time.sleep(0.05)
        time.sleep(5)
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    st = SessionSettings(output_dir=str(tmp_path), station_name="TEST",
                         camera_source=f"file:{session / 'camera' / 'cam0_000.avi'}", fps=8.0,
                         adsb_address=f"127.0.0.1:{port}", ogn_online=False)
    rec = SessionRecorder(st)
    folder = rec.start()
    time.sleep(3.0)
    assert rec.camera.preview is not None and rec.camera.status.sharpness > 0
    rec.stop()
    srv.close()

    meta = json.loads((folder / "session.json").read_text())
    assert meta["format"] == "ota-session/1" and meta["ended_utc"]
    assert meta["camera"]["segments"] == ["cam0_000.avi"]
    assert meta["stats"]["camera"]["written"] >= 15
    assert meta["stats"]["traffic"]["aircraft"] == 1
    stamps = (folder / "camera" / "cam0.timestamps.csv").read_text().splitlines()
    assert stamps[0] == "segment,frame,t_wall,t_rel"
    assert len(stamps) - 1 == meta["stats"]["camera"]["written"]
    t_wall = [float(r.split(",")[2]) for r in stamps[1:]]
    assert all(b > a for a, b in zip(t_wall, t_wall[1:]))
    traffic = (folder / "traffic.jsonl").read_text().splitlines()
    assert len(traffic) >= 20 and json.loads(traffic[0])["src"] == "adsb"
    assert (folder / "raw" / "adsb_sbs.txt").exists()
    assert "session" in (folder / "recorder.log").read_text()
