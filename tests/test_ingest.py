"""ota ingest / review / dataset on synthetic sessions."""

import shutil

import cv2
import numpy as np
import pytest

from ota.ingest import Archive, session_of
from ota.synth import make_synthetic_session


@pytest.fixture(scope="module")
def incoming(tmp_path_factory):
    usb = tmp_path_factory.mktemp("usb")
    make_synthetic_session(usb, duration_s=20.0, fps=8.0)
    return usb


def test_ingest_folder_evaluates_and_fills_dataset(incoming, tmp_path):
    ar = Archive(tmp_path / "archive")
    new = ar.ingest_folder(incoming, club="club-a")
    assert new == ["20261020-080000Z_SYNTH"]
    row = ar.known("20261020-080000Z_SYNTH")
    assert row["club"] == "club-a" and row["evaluated_utc"] and not row["eval_error"]
    assert row["max_track_km"] > 4 and row["matched_tracks"] >= 2
    assert row["heading_corr_deg"] == pytest.approx(-2.0, abs=0.4)
    assert list((tmp_path / "archive" / "dataset" / "aircraft").glob("*.png"))
    review = ar.review_items()
    assert review  # the non-cooperative paraglider
    assert (tmp_path / "archive" / "catalog.csv").read_text().count("\n") == 2
    # running again imports nothing new
    assert ar.ingest_folder(incoming, club="club-a") == []

    # labelling + undo
    p = review[0]
    q = ar.label(p, "bird")
    assert q.exists() and not p.exists() and q.parent.name == "bird"
    back = ar.unlabel(q)
    assert back == p and p.exists()
    assert (tmp_path / "archive" / "labels.csv").read_text().count("\n") == 2
    ar.close()


def test_dataset_split_by_session(tmp_path):
    ar = Archive(tmp_path / "archive")
    img = np.zeros((64, 64, 3), np.uint8)
    sessions = [f"2026102{i}-080000Z_S{i}" for i in range(5)]
    for s in sessions:
        for lab in ("aircraft", "bird", "background"):
            for k in range(3):
                cv2.imwrite(str(tmp_path / "archive" / "dataset" / lab / f"{s}_x_trk{k}_1.00.png"), img)
    counts = ar.export_dataset(tmp_path / "out", val_fraction=0.4)
    val = set(counts["val_sessions"])
    assert len(val) == 2
    for split in ("train", "val"):
        names = [p.name for p in (tmp_path / "out" / split).rglob("*.png")]
        assert all((session_of(n) in val) == (split == "val") for n in names)
    assert counts["train"]["aircraft"] == 9 and counts["val"]["bird"] == 6
    assert session_of("20261020-080000Z_LIPB_icao-4CA2D6_trk3_123.40.png") == "20261020-080000Z_LIPB"
    ar.close()


def test_ingest_from_s3(incoming, tmp_path):
    pytest.importorskip("boto3")
    moto_server = pytest.importorskip("moto.server")
    from ota.recorder.upload import SessionUploader, UploadConfig

    srv = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    srv.start()
    try:
        _, port = srv.get_host_and_port()
        cfg = UploadConfig(f"http://127.0.0.1:{port}", "ota", "k", "s", "us-east-1", "club-b")
        cfg.client().create_bucket(Bucket="ota")
        src = tmp_path / "member" / "20261020-080000Z_SYNTH"
        shutil.copytree(incoming / "20261020-080000Z_SYNTH", src)
        assert SessionUploader(src, cfg).run()
        reader = UploadConfig(cfg.endpoint, "ota", "k", "s", "us-east-1", "")  # lead's read key
        ar = Archive(tmp_path / "archive")
        assert ar.ingest_s3(reader, evaluate=False) == ["20261020-080000Z_SYNTH"]
        got = tmp_path / "archive" / "sessions" / "club-b" / "20261020-080000Z_SYNTH"
        assert (got / "camera" / "cam0_000.avi").read_bytes() == (src / "camera" / "cam0_000.avi").read_bytes()
        assert ar.known("20261020-080000Z_SYNTH")["club"] == "club-b"
        assert ar.ingest_s3(reader, evaluate=False) == []
        ar.close()
    finally:
        srv.stop()


def test_review_app_labels_whole_track(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    from ota.review import ReviewApp
    ar = Archive(tmp_path / "archive")
    img = np.full((64, 64, 3), 128, np.uint8)
    for trk, n in ((1, 3), (2, 2)):
        for k in range(n):
            cv2.imwrite(str(tmp_path / "archive" / "dataset" / "review" /
                            f"20261020-080000Z_X_unmatched_trk{trk}_{k}.00.png"), img)
    app = ReviewApp(root, ar)
    app.decide("bird", whole_track=True)        # all 3 crops of track 1
    assert len(list((tmp_path / "archive" / "dataset" / "bird").glob("*.png"))) == 3
    app.decide("background", whole_track=False)  # one crop of track 2
    app.undo()
    assert len(ar.review_items()) == 2
    root.destroy()
    ar.close()
