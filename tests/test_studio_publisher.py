"""Publisher foundation tests; no browser login or real YouTube upload."""
import json
from pathlib import Path
from queue import Queue
import sys
from types import ModuleType
from types import SimpleNamespace
from threading import Event

import pytest

from te.studio.publisher import PublisherStore, file_identity, normalize_metadata, validate_schedule
from te.studio.publisher_gui import PublisherController
from te.studio.service import StudioService
from te.studio.youtube_studio import YouTubeStudioBrowser, StudioSessionExpired, StudioVerificationError

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


class FakeLocator:
    def __init__(self, *, visible=True, click_error=None):
        self.first = self
        self.click_error = click_error
    def wait_for(self, **kwargs):
        if self.click_error:
            raise self.click_error
    def click(self):
        if self.click_error:
            raise self.click_error


class FakePage:
    def __init__(self, url, create_error=None):
        self.url = url
        self.create_error = create_error
    def goto(self, url, **kwargs):
        pass
    def wait_for_url(self, pattern, **kwargs):
        if self.url == "login":
            raise TimeoutError()
    def locator(self, selector):
        return FakeLocator(click_error=self.create_error)
    def get_by_text(self, selector):
        return FakeLocator()


def test_studio_channel_verification_does_not_infer_permission():
    browser = YouTubeStudioBrowser(Path("unused"))
    with pytest.raises(StudioSessionExpired):
        browser._verify_page(FakePage("login"), CID)
    with pytest.raises(StudioVerificationError, match="kênh khác"):
        browser._verify_page(FakePage("https://studio.youtube.com/channel/" + OTHER), CID)
    with pytest.raises(StudioVerificationError, match="Upload"):
        browser._verify_page(FakePage("https://studio.youtube.com/channel/" + CID, RuntimeError("denied")), CID)
    assert browser._verify_page(FakePage("https://studio.youtube.com/channel/" + CID), CID)["id"] == CID


def test_gui_browser_work_is_backgrounded_without_editor_lock():
    called = Event()
    class Root:
        def after(self, delay, callback):
            pass
    app = SimpleNamespace(root=Root())
    controller = object.__new__(PublisherController)
    controller.app = app
    controller.events = Queue()
    controller.busy = False
    controller.status = SimpleNamespace(set=lambda value: None)
    controller._start("verify", lambda: called.set() or {"id": CID})
    assert called.wait(2)
    assert controller.busy
    action, result, error = controller.events.get(timeout=2)
    assert (action, result["id"], error) == ("verify", CID, None)
    assert not hasattr(app, "_set_job_busy")

class UploadLocator:
    def __init__(self, name, calls, checked=True):
        self.first = self
        self.name = name
        self.calls = calls
        self.checked = checked
    def wait_for(self, **kwargs):
        self.calls.append((self.name, "wait", kwargs.get("state")))
    def click(self, **kwargs):
        self.calls.append((self.name, "click"))
    def fill(self, value):
        self.calls.append((self.name, "fill", value))
    def set_input_files(self, value):
        self.calls.append((self.name, "file", value))
    def get_attribute(self, key):
        if key == "aria-checked":
            return "true" if self.checked else "false"
        if key == "href":
            return "https://www.youtube.com/watch?v=abcdefghijk"
        return None
    def locator(self, selector):
        return UploadLocator(selector, self.calls, checked=self.checked)


class UploadPage:
    def __init__(self, calls, checked=True):
        self.calls = calls
        self.checked = checked
    def locator(self, selector):
        return UploadLocator(selector, self.calls, checked=self.checked)
    def get_by_text(self, selector):
        return UploadLocator("upload videos", self.calls, checked=self.checked)


class UploadContext:
    def __init__(self, page, calls):
        self.pages = [page]
        self.calls = calls
    def close(self):
        self.calls.append(("context", "close"))


def test_mock_private_upload_fills_metadata_and_never_selects_public(monkeypatch):
    calls = []
    browser = YouTubeStudioBrowser(Path("unused"))
    context = UploadContext(UploadPage(calls), calls)
    monkeypatch.setattr(browser, "_context", lambda: (SimpleNamespace(stop=lambda: None), context))
    monkeypatch.setattr(browser, "_verify_page", lambda page, cid: {"id": cid})
    job = {"channel_id": CID, "source": {"path": "movie.mp4"},
           "metadata": {"title": "日本語題名", "description": "日本語説明", "audience": "not_made_for_kids"}}
    result = browser.upload_private(job)
    assert result == {"video_id": "abcdefghijk", "video_url": "https://www.youtube.com/watch?v=abcdefghijk",
                      "state": "private_test"}
    assert ("#title-textarea #textbox, #title-textarea [contenteditable=true]", "fill", "日本語題名") in calls
    assert ("#description-textarea #textbox, #description-textarea [contenteditable=true]", "fill", "日本語説明") in calls
    assert ("input[type=file]", "file", "movie.mp4") in calls
    assert ("tp-yt-paper-radio-button[name='PRIVATE']", "click") in calls
    assert not any("PUBLIC" in str(call) for call in calls)
    assert calls.index(("#done-button", "click")) < calls.index(("ytcp-uploads-dialog", "wait", "hidden"))


def test_mock_upload_rejects_unconfirmed_privacy_before_save(monkeypatch):
    calls = []
    browser = YouTubeStudioBrowser(Path("unused"))
    context = UploadContext(UploadPage(calls, checked=False), calls)
    monkeypatch.setattr(browser, "_context", lambda: (SimpleNamespace(stop=lambda: None), context))
    monkeypatch.setattr(browser, "_verify_page", lambda page, cid: {"id": cid})
    job = {"channel_id": CID, "source": {"path": "movie.mp4"},
           "metadata": {"title": "日本語題名", "description": "", "audience": "made_for_kids"}}
    with pytest.raises(StudioVerificationError, match="Riêng tư"):
        browser.upload_private(job)
    assert ("#done-button", "click") not in calls
    assert ("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS']", "click") in calls


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


def _fake_playwright(monkeypatch, launch):
    api = ModuleType("playwright.sync_api")
    package = ModuleType("playwright")
    stopped = []
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch),
                                 stop=lambda: stopped.append(True))
    api.sync_playwright = lambda: SimpleNamespace(start=lambda: playwright)
    package.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    return playwright, stopped


def test_installed_chrome_channel_keeps_dedicated_profile_without_download(monkeypatch, tmp_path):
    profile = tmp_path / "youtube-publisher" / "browser"
    profile.mkdir(parents=True)
    marker = profile / "Existing session"
    marker.write_text("keep", encoding="utf-8")
    calls = []
    context = object()
    def launch(**kwargs):
        calls.append(kwargs)
        return context
    expected, stopped = _fake_playwright(monkeypatch, launch)
    browser = YouTubeStudioBrowser(profile)
    for _ in range(2):
        playwright, opened = browser._context()
        assert playwright is expected and opened is context
    assert calls == [dict(user_data_dir=str(profile), channel="chrome", headless=False,
                          viewport={"width": 1280, "height": 850})] * 2
    assert profile != Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not stopped


@pytest.mark.parametrize("detail,expected", [
    ("Chromium distribution 'chrome' is not found", "Không tìm thấy Google Chrome"),
    ("Browser profile is already in use", "Không mở được Google Chrome"),
])
def test_chrome_launch_error_is_actionable_and_never_falls_back(monkeypatch, tmp_path, detail, expected):
    calls = []
    def launch(**kwargs):
        calls.append(kwargs)
        raise RuntimeError(detail)
    _, stopped = _fake_playwright(monkeypatch, launch)
    with pytest.raises(RuntimeError, match=expected):
        YouTubeStudioBrowser(tmp_path / "publisher-profile")._context()
    assert len(calls) == 1 and calls[0]["channel"] == "chrome"
    assert stopped == [True]
