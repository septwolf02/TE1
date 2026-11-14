"""Publisher foundation tests; no browser login or real YouTube upload."""
import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from threading import Event

import pytest

from te.studio.publisher import PublisherStore, file_identity, normalize_metadata, validate_schedule
from te.studio.publisher_gui import PublisherController
from te.studio.service import StudioService
from te.studio.youtube_studio import YouTubeStudioBrowser, StudioVerificationError

CID = "UC" + "a" * 22
OTHER = "UC" + "b" * 22


def service(tmp_path):
    svc = StudioService(tmp_path / "settings.json")
    svc.set_directories(tmp_path / "projects", tmp_path / "output")
    return svc


def test_project_metadata_unicode_and_legacy_preservation(tmp_path):
    svc = service(tmp_path)
    state = svc.create_project("ニュース")
    state.publisher.update(channel_id=CID, title="大谷翔平の物語", description="日本語の説明\n二行目",
                           tags="野球, 大谷", mode="scheduled", scheduled_local="2030-01-01 12:00")
    state.project.subtitle_style["background_opacity"] = 67
    svc.save_project(state)
    reopened = svc.open_project(state.directory)
    assert reopened.publisher["title"] == "大谷翔平の物語"
    assert reopened.publisher["description"] == "日本語の説明\n二行目"
    assert reopened.publisher["channel_id"] == CID
    assert reopened.project.subtitle_style["background_opacity"] == 67
    sidecar = state.directory / "studio.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data.pop("publisher")
    sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    legacy = svc.open_project(state.directory)
    assert legacy.publisher["status"] == "draft"
    assert legacy.project.subtitle_style["background_opacity"] == 67
    assert not (state.directory / "youtube-publisher").exists()


def test_schedule_timezone_and_mode(tmp_path):
    assert validate_schedule("2030-01-01 12:00", "Asia/Tokyo").startswith("2030-01-01T12:00:00+09:00")
    with pytest.raises(ValueError):
        validate_schedule("2030-01-01 12:00", "UTC")
    with pytest.raises(ValueError):
        validate_schedule("2020-01-01 12:00", "Asia/Tokyo")
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(CID, "テスト")
    meta = normalize_metadata({"channel_id": CID, "title": "題名", "mode": "scheduled",
                               "scheduled_local": "2030-01-01 12:00"})
    job = store.prepare("project", meta, {"path": "test.mp4", "sha256": "abc", "bytes": 4})
    assert job["metadata"]["mode"] == "scheduled"
    assert job["metadata"]["timezone"] == "Asia/Tokyo"


def test_channel_association_duplicate_and_crash_recovery(tmp_path):
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(CID, "テスト")
    meta = normalize_metadata({"channel_id": CID, "title": "動画"})
    identity = {"path": "test.mp4", "sha256": "abc", "bytes": 4}
    first = store.prepare("p1", meta, identity)
    assert store.prepare("p1", meta, identity)["id"] == first["id"]
    with pytest.raises(ValueError, match="xác minh"):
        store.prepare("p1", {**meta, "channel_id": OTHER}, identity)
    store.add_verified_channel(OTHER, "別")
    assert store.prepare("p1", {**meta, "channel_id": OTHER}, identity)["id"] != first["id"]
    store.update(first["id"], status="uploading", last_confirmed_operation="attempt_started")
    restarted = PublisherStore(tmp_path / "settings.json")
    job = restarted.latest("p1")
    assert job["channel_id"] == OTHER
    recovered = next(j for j in restarted.data["jobs"] if j["id"] == first["id"])
    assert recovered["status"] == "needs_attention"
    assert restarted.prepare("p1", meta, identity)["status"] == "needs_attention"
    assert recovered["video_id"] == ""


def test_video_identity_and_failure_record(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"synthetic MP4 identity")
    identity = file_identity(source)
    assert identity["bytes"] == source.stat().st_size
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(CID, "テスト")
    job = store.prepare("p", {"channel_id": CID, "title": "日本語"}, identity)
    store.update(job["id"], status="uploading")
    store.update(job["id"], status="needs_attention", error="session expired")
    assert PublisherStore(tmp_path / "settings.json").latest("p")["error"] == "session expired"
    assert store.prepare("p", {"channel_id": CID, "title": "日本語"}, identity)["id"] == job["id"]
    with pytest.raises(ValueError):
        store.update(job["id"], video_id="invented")


def test_system_browser_handoff_does_not_claim_auth_or_channel_permission(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("te.studio.youtube_studio.webbrowser.open", lambda url: opened.append(url) or True)
    browser = YouTubeStudioBrowser(tmp_path / "retired-profile")
    result = browser.connect()
    channel = browser.verify_channel(CID)
    assert opened == ["https://studio.youtube.com/", f"https://studio.youtube.com/channel/{CID}"]
    assert "chưa xác minh" in result
    assert channel["id"] == CID
    assert channel["upload_control_verified"] is False
    assert channel["authorization_verified"] is False
    assert not browser.profile_dir.exists()
    with pytest.raises(ValueError, match="định dạng"):
        browser.verify_channel("invalid")
    assert len(opened) == 2


def test_authentication_open_errors_fail_without_creating_upload(monkeypatch, tmp_path):
    browser = YouTubeStudioBrowser(tmp_path / "retired-profile")
    monkeypatch.setattr("te.studio.youtube_studio.webbrowser.open", lambda url: False)
    with pytest.raises(StudioVerificationError, match="trình duyệt hệ thống"):
        browser.connect()
    def blocked(url):
        raise OSError("browser unavailable")
    monkeypatch.setattr("te.studio.youtube_studio.webbrowser.open", blocked)
    with pytest.raises(StudioVerificationError, match="trình duyệt hệ thống"):
        browser.verify_channel(CID)
    assert not browser.profile_dir.exists()


def test_automated_upload_is_blocked_before_browser_or_job_mutation(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("te.studio.youtube_studio.webbrowser.open", lambda url: opened.append(url) or True)
    browser = YouTubeStudioBrowser(tmp_path / "retired-profile")
    with pytest.raises(StudioVerificationError, match="tạm khóa"):
        browser.upload_private({"channel_id": CID, "source": {"path": "movie.mp4"}})
    assert opened == []
    assert not browser.profile_dir.exists()
    controller = object.__new__(PublisherController)
    messages = []
    controller.status = SimpleNamespace(set=messages.append)
    controller.app = SimpleNamespace(root=object())
    monkeypatch.setattr("te.studio.publisher_gui.messagebox.showwarning",
                        lambda title, message, **kwargs: messages.append(message))
    controller.upload()
    assert len(messages) == 2 and all("tạm khóa" in message for message in messages)
    assert opened == []


def test_legacy_channels_and_jobs_survive_manual_browser_handoff(monkeypatch, tmp_path):
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(CID, "既存のチャンネル")
    job = store.prepare("project", {"channel_id": CID, "title": "既存の動画"},
                        {"path": "movie.mp4", "sha256": "old-hash", "bytes": 10})
    before = store.path.read_bytes()
    monkeypatch.setattr("te.studio.youtube_studio.webbrowser.open", lambda url: True)
    browser = YouTubeStudioBrowser(store.root / "browser")
    browser.connect()
    assert browser.verify_channel(CID)["authorization_verified"] is False
    with pytest.raises(StudioVerificationError, match="tạm khóa"):
        browser.upload_private(job)
    assert store.path.read_bytes() == before
    assert PublisherStore(tmp_path / "settings.json").latest("project")["id"] == job["id"]


def test_cross_process_upload_attempt_reservation(tmp_path):
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(CID, "テスト")
    job = store.prepare("p", {"channel_id": CID, "title": "題名"},
                        {"path": "movie.mp4", "sha256": "hash", "bytes": 2})
    assert store.begin_attempt(job["id"])["status"] == "uploading"
    with pytest.raises(ValueError, match="sẵn sàng"):
        store.begin_attempt(job["id"])
    restarted = PublisherStore(tmp_path / "settings.json")
    assert restarted.latest("p")["status"] == "needs_attention"
    assert (restarted.root / "attempts" / (job["fingerprint"] + ".lock")).is_file()


def test_completed_full_hd_is_suggested_without_overwriting_selection():
    controller = object.__new__(PublisherController)
    state = SimpleNamespace(project=SimpleNamespace(id="p"), last_final="old.mp4")
    controller.app = SimpleNamespace(state=state, queue=SimpleNamespace(jobs=lambda: [
        {"project_id": "other", "status": "completed", "output_path": "other.mp4"},
        {"project_id": "p", "status": "completed", "output_path": "new.mp4"}]))
    value = SimpleNamespace(text="", get=lambda: value.text, set=lambda text: setattr(value, "text", text))
    controller.values = {"video_path": value}
    controller.suggest_video()
    assert value.text == "new.mp4"
    value.text = "manual.mp4"
    controller.suggest_video()
    assert value.text == "manual.mp4"
