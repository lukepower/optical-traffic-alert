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
                         adsb_address=f"127.0.0.1:{port}", ogn_online=False, record_mode="all")
    rec = SessionRecorder(st)
    folder = rec.start()
    time.sleep(3.0)
    assert rec.camera.preview is not None and rec.camera.status.sharpness > 0
    rec.stop()
    srv.close()

    meta = json.loads((folder / "session.json").read_text())
    assert meta["format"] == "ota-session/1" and meta["ended_utc"]
    assert meta["camera"]["segment_files"] == {"0": "cam0_0000.avi"}
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


def test_smart_recording_keeps_only_chunks_near_traffic(session, tmp_path):
    """Traffic in view only between ~4 and ~6 s: chunks far from it are deleted,
    the idle stream is kept, and the evaluation can still read the session."""
    from ota.evaluate import SessionEvaluator
    from ota.recorder import SessionRecorder, SessionSettings

    lines = [ln.split("\t")[1] for ln in
             (session / "raw" / "adsb_sbs.txt").read_text().splitlines() if "4CA2D6" in ln]
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        time.sleep(4.0)
        for ln in lines[:8]:  # ~2 s of the approaching aircraft (in view of a 190 deg camera)
            c.sendall((ln + "\r\n").encode())
            time.sleep(0.25)
        time.sleep(30)

    threading.Thread(target=serve, daemon=True).start()
    st = SessionSettings(output_dir=str(tmp_path), station_name="SMART",
                         camera_source=f"file:{session / 'camera' / 'cam0_000.avi'}", fps=8.0,
                         adsb_address=f"127.0.0.1:{port}", ogn_online=False, record_mode="smart",
                         motion_trigger=False, chunk_s=2.0, pre_s=2.0, post_s=2.0,
                         idle_interval_s=1.0, cam_heading_deg=190.0, cam_pitch_deg=3.0,
                         trigger_radius_km=15.0)
    rec = SessionRecorder(st)
    folder = rec.start()
    time.sleep(25.0)
    rec.stop()
    srv.close()

    meta = json.loads((folder / "session.json").read_text())
    cam = meta["stats"]["camera"]
    assert meta["stats"]["triggers"].get("traffic", 0) >= 1
    assert cam["kept_s"] > 3 and cam["discarded_s"] > 3          # some kept, some deleted
    assert cam["idle_frames"] >= 12
    files = meta["camera"]["segment_files"]
    on_disk = sorted(p.name for p in (folder / "camera").glob("*.avi"))
    assert sorted(files.values()) == on_disk                     # deleted chunks are gone
    assert any(n.startswith("cam0_idle_") for n in on_disk)
    rows = (folder / "camera" / "cam0.timestamps.csv").read_text().splitlines()[1:]
    assert {r.split(",")[0] for r in rows} == set(files)          # no rows of deleted chunks

    ev = SessionEvaluator(folder, save_crops=False)
    times = [tw for _, _, _, tw, _ in ev.iter_frames()]
    assert times == sorted(times) and len(times) >= 20
    assert len(set(times)) == len(times)


def test_motion_trigger_ignores_static_specks():
    import numpy as np
    from ota.recorder.smart import MotionTrigger, TriggerLog
    from ota.synth import _draw_aircraft, _sky

    rng = np.random.default_rng(3)
    bg = _sky(1280, 720, 600, rng)
    trig = TriggerLog()
    m = MotionTrigger(trig, 1280, 720)
    t0 = 1000.0
    for i in range(8):  # only a static speck: never triggers
        img = bg.copy()
        _draw_aircraft(img, 400, 200, 4)
        m.process(t0 + i * 0.5, np.clip(img, 0, 255).astype(np.uint8))
    assert not trig.counts
    for i in range(8):  # a moving target: triggers
        img = bg.copy()
        _draw_aircraft(img, 400, 200, 4)
        _draw_aircraft(img, 700 + 12 * i, 250, 4)
        m.process(t0 + 10 + i * 0.5, np.clip(img, 0, 255).astype(np.uint8))
    assert trig.counts.get("motion", 0) >= 1
    assert trig.any_between(t0 + 10, t0 + 14)
    assert not trig.any_between(t0, t0 + 4)
