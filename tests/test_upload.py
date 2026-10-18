"""Upload to S3-compatible storage (tested against a local moto server) and copying."""

import json
import os

import pytest

boto3 = pytest.importorskip("boto3")
moto_server = pytest.importorskip("moto.server")

from ota.recorder.upload import (COMPLETE_FILE, SessionUploader, UploadConfig, copy_session,  # noqa: E402
                                 list_sessions, upload_status)


@pytest.fixture(scope="module")
def s3():
    # bind to 127.0.0.1 explicitly: Windows cannot connect to 0.0.0.0
    srv = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    srv.start()
    _, port = srv.get_host_and_port()
    cfg = UploadConfig(endpoint=f"http://127.0.0.1:{port}", bucket="ota-test", key_id="k", secret="s",
                       region="us-east-1", prefix="club-a")
    cfg.client().create_bucket(Bucket="ota-test")
    yield cfg
    srv.stop()


def make_session(tmp_path, big_mb=3):
    d = tmp_path / "20261020-074512Z_LIPB"
    (d / "camera").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"ended_utc": "x", "stats": {"duration_s": 60}}))
    (d / "traffic.jsonl").write_text('{"src":"adsb"}\n')
    (d / "camera" / "cam0_0000.avi").write_bytes(os.urandom(big_mb * 1024 * 1024 + 123))
    return d


def test_upload_code_roundtrip():
    cfg = UploadConfig("https://s3.eu-central-003.backblazeb2.com", "b", "id", "sec", prefix="x")
    code = cfg.to_code()
    assert code.startswith("ota1:")
    assert UploadConfig.from_code(" " + code[:20] + "\n" + code[20:]) == cfg
    with pytest.raises(ValueError):
        UploadConfig.from_code("hello")


def test_resumable_multipart_upload(s3, tmp_path):
    d = make_session(tmp_path, big_mb=17)
    part = 5 * 1024 * 1024  # S3 minimum part size -> 4 parts for the 17 MB video
    up = SessionUploader(d, s3, part_size=part)
    # stop after the second part of the video
    calls = []

    def stop_soon(pr):
        calls.append(pr.done_bytes)
        if pr.current.endswith(".avi") and len(calls) > 2:
            up.stop()
    up.on_progress = stop_soon
    assert up.run() is False
    assert upload_status(d) == "partial"
    state = json.loads((d / "upload_state.json").read_text())
    parts_before = len(state["files"]["camera/cam0_0000.avi"]["parts"])
    assert 0 < parts_before < 4

    up2 = SessionUploader(d, s3, part_size=part)
    assert up2.run() is True, up2.progress.error
    assert upload_status(d) == "done"
    assert up2.progress.done_bytes == up2.progress.total_bytes

    c = s3.client()
    keys = {o["Key"] for o in c.list_objects_v2(Bucket="ota-test")["Contents"]}
    base = "club-a/20261020-074512Z_LIPB"
    assert {f"{base}/session.json", f"{base}/traffic.jsonl", f"{base}/camera/cam0_0000.avi",
            f"{base}/{COMPLETE_FILE}"} <= keys
    body = c.get_object(Bucket="ota-test", Key=f"{base}/camera/cam0_0000.avi")["Body"].read()
    assert body == (d / "camera" / "cam0_0000.avi").read_bytes()
    manifest = json.loads(c.get_object(Bucket="ota-test", Key=f"{base}/{COMPLETE_FILE}")["Body"].read())
    assert manifest["files"]["camera/cam0_0000.avi"] == len(body)


def test_copy_and_list(tmp_path):
    d = make_session(tmp_path, big_mb=1)
    usb = tmp_path / "usb"
    usb.mkdir()
    out = copy_session(d, usb)
    assert (out / "camera" / "cam0_0000.avi").read_bytes() == (d / "camera" / "cam0_0000.avi").read_bytes()
    lst = list_sessions(tmp_path)
    assert lst[0]["name"] == d.name and lst[0]["bytes"] > 1024 * 1024 and lst[0]["upload"] == ""
