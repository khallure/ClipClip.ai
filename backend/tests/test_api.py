"""Local API/queue tests. Media codecs are exercised separately in test_media.py.

These tests never download a Whisper model, contact OpenAI or access YouTube.
"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import analysis, config, media
from app.main import create_app
from app.store import ACTIVE, JobStore


MEDIA = {"duration": 60.0, "width": 640, "height": 360, "fps": 25.0,
         "video_codec": "h264", "audio_codec": "aac", "has_audio": True}
SETTINGS = {"clip_count": 3, "min_duration": 20, "max_duration": 60,
            "target_duration": 40, "language": "auto"}
TRANSCRIPT = {"language": "id", "segments": [
    {"start": 0, "end": 10, "text": "Ternyata penyebabnya bukan kurang waktu, melainkan tidak mengetahui tindakan pertama yang bisa dilakukan saat pekerjaan besar terasa sangat berat."},
    {"start": 10, "end": 20, "text": "Karena tugas tidak memiliki langkah yang jelas, kita menunda dan menghabiskan tenaga memikirkan hasil akhir yang terlalu besar untuk dimulai."},
    {"start": 20, "end": 30, "text": "Jadi pecah pekerjaan menjadi satu tindakan kecil, misalnya membuka dokumen dan menulis satu kalimat sebelum kopi selesai diseduh di dapur."},
    {"start": 30, "end": 40, "text": "Intinya mulai dari langkah yang terlihat dan periksa hasilnya, lalu tingkatkan target sedikit demi sedikit tanpa menunggu motivasi datang sendiri."},
]}


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(media, "probe", Mock(return_value=dict(MEDIA)))
    monkeypatch.setattr(media, "make_poster", Mock())
    # Any accidental transcription request fails locally instead of downloading.
    monkeypatch.setattr(media, "transcribe", Mock(side_effect=media.MediaError("Local transcription is not installed. Import a timestamped transcript, then retry.")))
    return create_app(tmp_path / "data")


@pytest.fixture
def client(local_app):
    with TestClient(local_app) as test_client:
        yield test_client


def new_job(client, **fields):
    response = client.post("/api/jobs", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def attach_source(client, ident, filename="owned.mp4"):
    response = client.post(f"/api/jobs/{ident}/source", files={"file": (filename, b"fake-video-bytes", "video/mp4")})
    assert response.status_code == 200, response.text
    return response.json()


def import_transcript(client, ident, transcript=None):
    response = client.post(f"/api/jobs/{ident}/transcript", files={"file": ("speech.json", json.dumps(transcript or TRANSCRIPT, ensure_ascii=False).encode(), "application/json")})
    assert response.status_code == 200, response.text
    return response.json()


def prepared(client):
    job = new_job(client)
    attach_source(client, job["id"])
    return import_transcript(client, job["id"])


def wait_status(client, ident, expected, timeout=5):
    expected = {expected} if isinstance(expected, str) else set(expected)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{ident}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in expected:
            return job
        time.sleep(.01)
    pytest.fail(f"Project did not reach {expected}; last state was {job}")


def test_health_reports_missing_dependencies_and_never_a_secret(client, monkeypatch):
    monkeypatch.setattr(config, "binary", lambda name: None)
    monkeypatch.setattr(media, "available_whisper", lambda: False)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ffmpeg"] is False
    assert response.json()["ffprobe"] is False
    assert response.json()["whisper"] is False
    assert response.json()["openai"] is False
    assert response.headers["x-content-type-options"] == "nosniff"
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-value")
    response = client.get("/api/health")
    assert response.json()["openai"] is False
    assert "private-test-value" not in response.text


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk&t=10",
    "https://youtu.be/abcdefghijk", "https://m.youtube.com/watch?v=abcdefghijk",
    "https://www.youtube.com/shorts/abcdefghijk", "https://www.youtube.com/live/abcdefghijk",
])
def test_creating_youtube_project_preserves_canonical_url_without_claiming_access(client, url):
    job = new_job(client, youtube_url=url)
    assert job["youtube_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert job["status"] == "awaiting_upload"
    assert job["media"] is None
    assert "memeriksa akses" in job["message"]
    assert not job.get("source_error")  # Access has not been tested at project creation.
    response = client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS)
    assert response.status_code == 409
    attached = attach_source(client, job["id"])
    assert attached["id"] == job["id"]
    assert attached["youtube_url"] == job["youtube_url"]
    assert attached["status"] == "uploaded"
    media.transcribe.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=abcdefghijk", "http://127.0.0.1/private",
    "https://169.254.169.254/latest/meta-data", "file:///C:/Windows/win.ini",
    "https://www.youtube.com.evil.example/watch?v=abcdefghijk",
    "https://youtube.com@evil.example/watch?v=abcdefghijk",
    "https://user:password@youtube.com/watch?v=abcdefghijk",
    "https://www.youtube.com:8443/watch?v=abcdefghijk",
    "https://www.youtube.com/playlist?list=abcdefghijk", "https://youtu.be/too-short",
])
def test_arbitrary_url_and_ssrf_targets_rejected(client, url):
    response = client.post("/api/jobs", json={"youtube_url": url})
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_extra_request_fields_and_bad_ids_rejected(client):
    assert client.post("/api/jobs", json={"api_key": "do-not-store"}).status_code == 422
    assert client.post("/api/jobs", json={"title": "   "}).status_code == 422
    assert client.get("/api/jobs/not-a-job").status_code == 404
    assert client.get("/api/jobs/" + "a" * 32).status_code == 404
    assert client.get("/api/jobs/%2E%2E%5Csecret").status_code == 404


def test_remote_origin_cannot_mutate_local_projects(client):
    response = client.post("/api/jobs", json={}, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert client.get("/api/jobs").json() == []
    assert client.post("/api/jobs", json={}, headers={"Origin": "http://localhost:3000"}).status_code == 403
    assert client.post("/api/jobs", json={}, headers={"Origin": "http://localhost:3300"}).status_code == 201
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400


def test_source_filename_is_sanitized_and_source_cannot_be_replaced(client, local_app):
    job = new_job(client)
    attached = attach_source(client, job["id"], "../../elsewhere/episode.mp4")
    assert attached["source_name"] == "episode.mp4"
    directory = local_app.state.store.directory(job["id"])
    assert (directory / "source.mp4").read_bytes() == b"fake-video-bytes"
    assert not any(key.startswith("_") for key in attached)
    assert client.post(f"/api/jobs/{job['id']}/source", files={"file": ("next.mp4", b"another")}).status_code == 409


def test_source_playback_supports_range_requests(client):
    job = new_job(client)
    attach_source(client, job["id"])
    response = client.get(f"/api/jobs/{job['id']}/media", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"ke-v"
    assert response.headers["content-range"] == "bytes 2-5/16"


def test_unsupported_source_does_not_enter_processing(client):
    job = new_job(client)
    response = client.post(f"/api/jobs/{job['id']}/source", files={"file": ("playlist.m3u8", b"#EXTM3U")})
    assert response.status_code == 422
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "awaiting_upload"


def test_upload_limit_is_enforced_before_probe(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    job = new_job(client)
    response = client.post(f"/api/jobs/{job['id']}/source", files={"file": ("large.mp4", b"x" * (2 * 1024 ** 2))})
    assert response.status_code == 413
    media.probe.assert_not_called()


def test_missing_ffprobe_is_actionable_and_source_can_be_reattached(client, local_app):
    job = new_job(client, youtube_url="https://youtu.be/abcdefghijk")
    media.probe.side_effect = media.MediaError("FFPROBE is unavailable. Install FFmpeg (including FFprobe), then retry this project.")
    response = client.post(f"/api/jobs/{job['id']}/source", files={"file": ("owned.mp4", b"bytes")})
    assert response.status_code == 422
    blocked = client.get(f"/api/jobs/{job['id']}").json()
    assert blocked["status"] == "blocked"
    assert "FFPROBE" in blocked["error"]
    assert not list(local_app.state.store.directory(job["id"]).glob("*.partial*"))
    media.probe.side_effect = None
    attached = attach_source(client, job["id"])
    assert attached["youtube_url"] == job["youtube_url"]


def test_unsupported_browser_codec_has_clear_warning(client):
    media.probe.return_value = {**MEDIA, "video_codec": "mpeg2video", "audio_codec": "pcm_s16le"}
    job = new_job(client)
    uploaded = attach_source(client, job["id"], "licensed.mov")
    assert uploaded["status"] == "uploaded"
    assert any("may not play" in warning for warning in uploaded["warnings"])


def test_two_hour_plus_metadata_is_accepted_without_decoding_into_memory(client):
    media.probe.return_value = {**MEDIA, "duration": 7800.0}
    job = new_job(client)
    uploaded = attach_source(client, job["id"])
    assert uploaded["media"]["duration"] == 7800
    media.transcribe.assert_not_called()


def test_no_audio_source_explains_recovery_and_accepts_transcript(client):
    media.probe.return_value = {**MEDIA, "has_audio": False, "audio_codec": None}
    job = new_job(client)
    uploaded = attach_source(client, job["id"])
    assert any("no audio" in warning.lower() for warning in uploaded["warnings"])
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 200
    blocked = wait_status(client, job["id"], "blocked")
    assert "no audio" in blocked["error"].lower()
    import_transcript(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    assert wait_status(client, job["id"], "ready")["candidates"]
    media.transcribe.assert_not_called()


def test_transcript_requires_source_and_valid_import_is_preserved(client, local_app):
    job = new_job(client)
    response = client.post(f"/api/jobs/{job['id']}/transcript", files={"file": ("speech.json", json.dumps(TRANSCRIPT))})
    assert response.status_code == 409
    attach_source(client, job["id"])
    imported = import_transcript(client, job["id"])
    assert imported["transcript_ready"] is True
    original = client.get(f"/api/jobs/{job['id']}/transcript").json()
    response = client.post(f"/api/jobs/{job['id']}/transcript", files={"file": ("bad.json", b"not json")})
    assert response.status_code == 422
    assert client.get(f"/api/jobs/{job['id']}/transcript").json() == original
    assert local_app.state.store.get(job["id"])["_transcript_imported"] is True


@pytest.mark.parametrize("payload", [
    {"segments": []}, {"segments": "not a list"}, {"other": []},
    {"segments": [{"start": -1, "end": 10, "text": "Bad"}]},
    {"segments": [{"start": 10, "end": 5, "text": "Bad"}]},
    {"segments": [{"start": 50, "end": 70, "text": "Outside source"}]},
    {"segments": [{"start": 0, "end": 10, "text": ""}]},
    {"segments": [{"start": 10, "end": 20, "text": "Second"}, {"start": 0, "end": 5, "text": "First"}]},
    {"segments": [{"start": 0, "end": 10, "text": "Bad words", "words": [{"start": 5, "end": 15, "word": "Bad"}]}]},
])
def test_invalid_transcript_timestamps_rejected(client, payload):
    job = new_job(client)
    attach_source(client, job["id"])
    response = client.post(f"/api/jobs/{job['id']}/transcript", files={"file": ("bad.json", json.dumps(payload))})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/jobs/{job['id']}/transcript").status_code == 404


def test_overlapping_word_times_rejected_at_import_not_during_analysis(client):
    job = new_job(client)
    attach_source(client, job["id"])
    payload = {"segments": [{"start": 0, "end": 30, "text": "An actual hook with overlap.",
                             "words": [{"start": 0, "end": 15, "word": "An actual"},
                                       {"start": 10, "end": 30, "word": "hook with overlap."}]}]}
    response = client.post(f"/api/jobs/{job['id']}/transcript", files={"file": ("bad.json", json.dumps(payload))})
    assert response.status_code == 422


def test_srt_import_preserves_times_and_downloads_browser_captions(client):
    job = new_job(client)
    attach_source(client, job["id"])
    text = "1\n00:00:01,250 --> 00:00:12,500\nTernyata ini kata asli.\n\n2\n00:00:15,000 --> 00:00:35,000\nKarena konteks sangat penting.\n"
    response = client.post(f"/api/jobs/{job['id']}/transcript", files={"file": ("speech.srt", text)})
    assert response.status_code == 200
    transcript = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert transcript["segments"][0]["start"] == 1.25
    assert transcript["segments"][0]["end"] == 12.5
    assert transcript["word_timestamps"] is False
    vtt = client.get(f"/api/jobs/{job['id']}/captions.vtt")
    assert vtt.status_code == 200
    assert vtt.text.startswith("WEBVTT")
    assert "00:00:01.250 --> 00:00:12.500" in vtt.text


@pytest.mark.parametrize("changes", [
    {"clip_count": 2}, {"clip_count": 11}, {"min_duration": 19}, {"max_duration": 91},
    {"target_duration": 80, "max_duration": 60}, {"language": "translate"},
    {"manual_start": 5}, {"manual_start": -1, "manual_end": 40},
    {"manual_start": 10, "manual_end": 20}, {"manual_start": 20, "manual_end": 70},
    {"model": "untrusted-model"},
])
def test_analysis_settings_are_validated_before_queue(client, changes):
    job = prepared(client)
    response = client.post(f"/api/jobs/{job['id']}/analyze", json={**SETTINGS, **changes})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "uploaded"


def test_no_key_analysis_is_honestly_labelled_and_import_avoids_whisper(client):
    job = prepared(client)
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 200
    ready = wait_status(client, job["id"], "ready")
    assert ready["analysis_mode"] == "heuristic"
    assert ready["candidates"]
    assert all(c["review_mode"] == "heuristic" for c in ready["candidates"])
    assert any("No OpenAI credentials" in warning for warning in ready["warnings"])
    assert ready["progress"] == 100
    assert all(not key.startswith("_") for key in ready)
    media.transcribe.assert_not_called()


def test_missing_whisper_blocks_then_import_and_retry_recovers_same_job(client):
    job = new_job(client)
    attach_source(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 200
    blocked = wait_status(client, job["id"], "blocked")
    assert "transcript" in blocked["error"]
    import_transcript(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    ready = wait_status(client, job["id"], "ready")
    assert ready["id"] == job["id"]
    assert ready["source_name"] == "owned.mp4"


def test_identical_settings_reuse_analysis_cache_changed_length_reuses_transcript(client, monkeypatch):
    job = prepared(client)
    real = analysis.analyze_transcript
    spy = Mock(wraps=real)
    monkeypatch.setattr(analysis, "analyze_transcript", spy)
    for settings in [SETTINGS, SETTINGS, {**SETTINGS, "target_duration": 30}]:
        assert client.post(f"/api/jobs/{job['id']}/analyze", json=settings).status_code == 200
        wait_status(client, job["id"], "ready")
    assert spy.call_count == 2
    media.transcribe.assert_not_called()


def test_cancel_and_retry_preserve_previous_cards_and_files(client, local_app, monkeypatch):
    job = prepared(client)
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 200
    first = wait_status(client, job["id"], "ready")
    entered = threading.Event()
    real = analysis.analyze_transcript
    def interruptible(*args, **kwargs):
        entered.set()
        deadline = time.monotonic() + 4
        while not kwargs["cancelled"]() and time.monotonic() < deadline:
            time.sleep(.01)
        raise analysis.AnalysisCancelled("Analysis cancelled.")
    monkeypatch.setattr(analysis, "analyze_transcript", interruptible)
    assert client.post(f"/api/jobs/{job['id']}/analyze", json={**SETTINGS, "target_duration": 30}).status_code == 200
    assert entered.wait(2)
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    cancelled = wait_status(client, job["id"], "cancelled")
    assert cancelled["candidates"] == first["candidates"]
    directory = local_app.state.store.directory(job["id"])
    assert (directory / "source.mp4").is_file()
    assert (directory / "transcript.json").is_file()
    monkeypatch.setattr(analysis, "analyze_transcript", real)
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    ready = wait_status(client, job["id"], "ready")
    assert ready["settings"]["target_duration"] == 30
    assert ready["candidates"]


def test_cancel_queued_job_is_durable_and_retriable(client, monkeypatch):
    first, second = prepared(client), prepared(client)
    entered, release = threading.Event(), threading.Event()
    real = analysis.analyze_transcript
    calls = 0
    def block_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            release.wait(3)
        return real(*args, **kwargs)
    monkeypatch.setattr(analysis, "analyze_transcript", block_first)
    assert client.post(f"/api/jobs/{first['id']}/analyze", json=SETTINGS).status_code == 200
    assert entered.wait(2)
    try:
        assert client.post(f"/api/jobs/{second['id']}/analyze", json=SETTINGS).status_code == 200
        assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "queued"
        assert client.post(f"/api/jobs/{second['id']}/cancel").status_code == 200
    finally:
        release.set()
    wait_status(client, first["id"], "ready")
    wait_status(client, second["id"], "cancelled")
    assert calls == 1
    assert client.post(f"/api/jobs/{second['id']}/retry").status_code == 200
    wait_status(client, second["id"], "ready")
    assert calls == 2


@pytest.mark.parametrize("payload", [
    {"start": -1, "end": 20}, {"start": 20, "end": 10},
    {"start": 0, "end": 70}, {"start": 0, "end": 10, "aspect": "4k"},
    {"start": 0, "end": 10, "crop_x": 1.5}, {"start": 0, "end": 10, "caption_style": "script"},
    {"start": 0, "end": 10, "filename": "../../secret.mp4"},
])
def test_export_parameters_validated_before_queue(client, payload):
    job = prepared(client)
    response = client.post(f"/api/jobs/{job['id']}/exports", json=payload)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/jobs/{job['id']}").json()["exports"] == []


def test_burn_in_requires_timestamped_transcript(client):
    job = new_job(client)
    attach_source(client, job["id"])
    response = client.post(f"/api/jobs/{job['id']}/exports", json={"start": 0, "end": 10, "caption_style": "clean"})
    assert response.status_code == 409


def test_failed_export_retry_preserves_cards_and_never_retranscribes(client, monkeypatch):
    job = prepared(client)
    assert client.post(f"/api/jobs/{job['id']}/analyze", json=SETTINGS).status_code == 200
    first = wait_status(client, job["id"], "ready")
    encoder = Mock(side_effect=media.MediaError("FFMPEG is unavailable. Install FFmpeg, then retry."))
    monkeypatch.setattr(media, "export_clip", encoder)
    analysis_spy = Mock(wraps=analysis.analyze_transcript)
    monkeypatch.setattr(analysis, "analyze_transcript", analysis_spy)
    response = client.post(f"/api/jobs/{job['id']}/exports", json={"start": 2.125, "end": 25.625, "aspect": "vertical", "crop_x": .25})
    assert response.status_code == 202
    blocked = wait_status(client, job["id"], "blocked")
    assert blocked["candidates"] == first["candidates"]
    assert blocked["exports"][0]["status"] == "failed"
    export_id = blocked["exports"][0]["id"]
    assert client.get(f"/api/jobs/{job['id']}/exports/{export_id}/file").status_code == 409
    def encoded(source, directory, settings, transcript, source_info, cancelled):
        # This API test stubs the encoder; real codecs are verified in test_media.
        assert settings["start"] == 2.125 and settings["end"] == 25.625
        assert transcript["segments"][0]["text"] == TRANSCRIPT["segments"][0]["text"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "clip.mp4").write_bytes(b"encoded-output-placeholder")
        (directory / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest.\n", encoding="utf-8")
        return {**MEDIA, "duration": 23.5, "width": 1080, "height": 1920}
    encoder.side_effect = encoded
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    ready = wait_status(client, job["id"], "ready")
    assert len(ready["exports"]) == 1
    assert ready["exports"][0]["id"] == export_id
    assert ready["exports"][0]["status"] == "ready"
    assert (ready["exports"][0]["width"], ready["exports"][0]["height"]) == (1080, 1920)
    assert ready["candidates"] == first["candidates"]
    assert client.get(f"/api/jobs/{job['id']}/exports/{export_id}/file").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}/exports/{export_id}/captions.srt").status_code == 200
    media.transcribe.assert_not_called()
    analysis_spy.assert_not_called()


def test_download_gate_does_not_claim_missing_or_unverified_exports_ready(client, local_app):
    job = prepared(client)
    ident, export_id = job["id"], "b" * 32
    assert client.get(f"/api/jobs/{ident}/exports/{export_id}/file").status_code == 409
    local_app.state.store.update(ident, exports=[{"id": export_id, "status": "processing"}])
    assert client.get(f"/api/jobs/{ident}/exports/{export_id}/file").status_code == 409
    local_app.state.store.update(ident, exports=[{"id": export_id, "status": "ready"}])
    assert client.get(f"/api/jobs/{ident}/exports/{export_id}/file").status_code == 404


def test_queue_full_export_does_not_leave_orphan_queued_record(local_app):
    # Stop the worker after entering the lifespan, leaving a bounded queue idle.
    with TestClient(local_app, raise_server_exceptions=False) as running_client:
        # A genuinely active worker would consume items. Stop it first, then fill.
        local_app.state.pipeline.close()
        job = prepared(running_client)
        for _ in range(local_app.state.pipeline.tasks.maxsize):
            local_app.state.pipeline.tasks.put_nowait(("not-started", {}))
        response = running_client.post(f"/api/jobs/{job['id']}/exports", json={"start": 0, "end": 10})
        assert response.status_code == 503
        state = running_client.get(f"/api/jobs/{job['id']}").json()
        assert state["exports"] == []
        assert state["status"] == "uploaded"


def test_history_delete_removes_only_selected_project(client, local_app):
    first, second = prepared(client), prepared(client)
    first_path = local_app.state.store.directory(first["id"])
    second_path = local_app.state.store.directory(second["id"])
    assert len(client.get("/api/jobs").json()) == 2
    assert client.delete(f"/api/jobs/{first['id']}").json() == {"deleted": True}
    assert not first_path.exists()
    assert second_path.is_dir()
    assert client.get(f"/api/jobs/{first['id']}").status_code == 404


def test_storage_exhaustion_is_actionable(client, local_app, monkeypatch):
    monkeypatch.setattr(local_app.state.store, "ensure_capacity", Mock(side_effect=ValueError("Local storage limit reached. Delete unused projects.")))
    response = client.post("/api/jobs", json={})
    assert response.status_code == 507
    assert "storage" in response.json()["detail"]


def test_restart_marks_interrupted_work_blocked_preserving_prior_outputs(tmp_path):
    store = JobStore(tmp_path / "durable")
    job = store.create("An interrupted project", None)
    ident = job["id"]
    source = store.directory(ident) / "source.mp4"
    source.write_bytes(b"preserved source")
    completed = store.directory(ident) / "exports" / ("a" * 32) / "clip.mp4"
    completed.parent.mkdir(parents=True)
    completed.write_bytes(b"preserved export")
    partial = store.directory(ident) / "source.partial.mp4"
    partial.write_bytes(b"incomplete")
    store.update(ident, status="exporting", candidates=[{"id": "existing-card"}], exports=[
        {"id": "a" * 32, "status": "ready"}, {"id": "b" * 32, "status": "processing"},
    ])
    store.recover()
    recovered = store.get(ident)
    assert recovered["status"] == "blocked"
    assert recovered["candidates"] == [{"id": "existing-card"}]
    assert recovered["exports"][0]["status"] == "ready"
    assert recovered["exports"][1]["status"] == "failed"
    assert source.is_file() and completed.is_file()
    assert not partial.exists()


def test_disk_cleanup_removes_only_idle_disposable_audio_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_LIMIT_GB", .005)
    store = JobStore(tmp_path / "limited")
    job = store.create("Preserve important files", None)
    directory = store.directory(job["id"])
    source, transcript, audio = directory / "source.mp4", directory / "transcript.json", directory / "audio.wav"
    source.write_bytes(b"s" * (3 * 1024 ** 2))
    audio.write_bytes(b"a" * (3 * 1024 ** 2))
    transcript.write_text(json.dumps(TRANSCRIPT), encoding="utf-8")
    completed = directory / "exports" / ("b" * 32) / "clip.mp4"
    completed.parent.mkdir(parents=True)
    completed.write_bytes(b"e" * (1024 ** 2))
    store.ensure_capacity()
    assert not audio.exists()
    assert source.is_file() and transcript.is_file() and completed.is_file()
    # An active job owns its scratch audio: do not delete it underneath Whisper.
    audio.write_bytes(b"a" * (3 * 1024 ** 2))
    store.update(job["id"], status="transcribing")
    with pytest.raises(ValueError, match="storage"):
        store.ensure_capacity()
    assert audio.is_file()


def test_nonfinite_json_export_input_returns_validation_error(local_app):
    with TestClient(local_app, raise_server_exceptions=False) as test_client:
        job = prepared(test_client)
        response = test_client.post(f"/api/jobs/{job['id']}/exports", content='{"start":NaN,"end":20}', headers={"Content-Type": "application/json"})
        assert response.status_code == 422
