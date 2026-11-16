"""Project loading safety and diagnostics without a Tk display."""
import json
from types import SimpleNamespace

import pytest

from te.studio.gui import StudioApp, _write_project_open_diagnostic
from te.studio.publisher import PublisherStore, normalize_metadata
from te.studio.publisher_gui import PublisherController
from te.studio.studio_connector import StudioConnectorStore
from te.studio.service import StudioService


CHANNEL = "UC" + "a" * 22


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Combo:
    def __init__(self):
        self.values = []
        self.selection = ""
        self.index = -1

    def __setitem__(self, key, value):
        assert key == "values"
        self.values = value

    def current(self, index=None):
        if index is None:
            return self.index
        if index < 0 or index >= len(self.values):
            raise IndexError(f"Index {index} out of range")
        self.index = index
        self.selection = self.values[index]

    def set(self, value):
        self.selection = value
        self.index = -1


class Text:
    def delete(self, *args):
        self.value = ""

    def insert(self, *args):
        self.value = args[-1]


def service(tmp_path):
    repo = StudioService(tmp_path / "settings.json")
    repo.set_directories(tmp_path / "projects", tmp_path / "output")
    return repo


def publisher_harness(repo, state):
    controller = PublisherController.__new__(PublisherController)
    controller.app = SimpleNamespace(queue=SimpleNamespace(jobs=lambda: []))
    controller.store = PublisherStore(repo.config_path)
    controller.studio_store = StudioConnectorStore(repo.config_path)
    controller.studio_status = Var()
    controller.values = {key: Var() for key in normalize_metadata({})}
    controller.description = Text()
    controller.channel_combo = Combo()
    controller.channel_name = Var()
    controller.oauth_status = Var()
    controller.status = Var()
    controller.load(state)
    return controller


def test_existing_and_legacy_projects_load_with_no_publisher_selection(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("Legacy")
    state.sections["P1"]["ja"] = "旧本文。"
    state.publisher["channel_id"] = CHANNEL
    repo.save_project(state)
    restored = repo.open_project(state.directory)
    controller = publisher_harness(repo, restored)
    assert restored.project.name == "Legacy"
    assert restored.sections["P1"]["ja"] == "旧本文。"
    assert controller.channel_combo.selection == ""
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller.channel_name.get() == ""
    assert controller.store.authorized_channels() == []

    controller.store.add_verified_channel(CHANNEL, "Kênh cũ")
    controller.load(restored)
    assert controller.channel_combo.index == 0
    assert controller.channel_name.get() == "Kênh cũ"
    controller.channel_records = []
    controller.channel_combo.set("")
    controller._select_channel()  # No Tk selection event may bind a phantom channel.
    assert controller.values["channel_id"].get() == CHANNEL


def test_empty_channel_list_and_missing_optional_metadata_load(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("No channel")
    sidecar = state.directory / "studio.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data.pop("publisher", None)
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    loaded = repo.open_project(state.directory)
    controller = publisher_harness(repo, loaded)
    assert controller.channel_combo.values == []
    assert controller.channel_combo.selection == ""
    assert controller.values["channel_id"].get() == ""


def test_studio_connector_selection_stays_separate_from_api_credentials(tmp_path):
    from te.studio.studio_connector import StudioConnectorStore
    from te.studio.youtube_api import SCOPES

    repo = service(tmp_path)
    state = repo.create_project("Two connections")
    controller = publisher_harness(repo, state)
    controller.store.register_authorized_channel({"id": CHANNEL, "name": "API channel",
        "credential_ref": "a" * 32, "scopes": list(SCOPES), "authorization_status": "authorized"})
    url = f"https://studio.youtube.com/channel/{CHANNEL}/videos"
    StudioConnectorStore(repo.config_path).save_message({"type": "scan_results", "schema": 1,
        "page_url": url, "page_url_after": url, "switcher_open": True,
        "channels": [{"id": CHANNEL, "name": "Studio channel", "source_url": url}]})
    controller.refresh_channels()
    assert len(controller.channel_records) == 2
    controller.channel_combo.current(1)
    controller._select_channel()
    assert controller.values["connection_type"].get() == "studio_connector"
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller._selected_connection() is None
    controller.channel_combo.current(0)
    controller._select_channel()
    assert controller.values["connection_type"].get() == "youtube_api"
    assert controller._selected_connection()["id"] == CHANNEL
    controller.channel_records.append({"id": "", "name": "Unresolved", "status": "discovered",
                                       "connection_type": "studio_connector"})
    controller.channel_combo["values"] = list(controller.channel_combo.values) + ["Unresolved"]
    controller.channel_combo.current(2)
    controller._select_channel()
    assert controller.values["connection_type"].get() == "youtube_api"
    assert controller.values["channel_id"].get() == CHANNEL


def test_user_named_studio_channel_can_be_selected_without_api_upload(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("Manual name")
    controller = publisher_harness(repo, state)
    url = f"https://studio.youtube.com/channel/{CHANNEL}/videos"
    controller.studio_store.save_message({"type": "scan_results", "schema": 1,
        "page_url": url, "page_url_after": url, "switcher_open": False,
        "channels": [{"id": CHANNEL, "name": "", "source_url": url}],
        "manual_name": "大谷翔平速報"})
    controller.refresh_channels()
    controller.channel_combo.current(0)
    controller._select_channel()
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller.values["connection_type"].get() == "studio_connector"
    assert controller._selected_connection() is None


def test_gui_hydration_failure_rolls_back_without_saving(tmp_path):
    repo = service(tmp_path)
    previous = repo.create_project("Previous")
    incoming = repo.create_project("Incoming")
    app = StudioApp.__new__(StudioApp)
    app.state = previous
    app._save_after = "pending"
    cancelled = []
    app.root = SimpleNamespace(after_cancel=cancelled.append)
    hydrated = []

    def hydrate(state):
        app.state = state
        hydrated.append(state.project.name)
        if state is incoming:
            raise IndexError("Index -1 out of range")

    app._hydrate_state = hydrate
    with pytest.raises(IndexError, match="Index -1"):
        app._load_state(incoming)
    assert hydrated == ["Incoming", "Previous"]
    assert app.state is previous
    assert cancelled == ["pending"] and app._save_after is None
    assert repo.open_project(incoming.directory).project.name == "Incoming"
    assert repo.open_project(previous.directory).project.name == "Previous"


def test_diagnostics_preserve_full_traceback_and_redact_credentials(tmp_path):
    def explode():
        raise RuntimeError("access_token=secret123 password=guessme Bearer opaque")

    try:
        explode()
    except RuntimeError as exc:
        path = _write_project_open_diagnostic(tmp_path / "settings.json", "Nạp giao diện dự án", exc)
    content = path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in content
    assert "explode" in content and "Nạp giao diện dự án" in content
    assert "secret123" not in content and "guessme" not in content and "opaque" not in content
    assert content.count("[REDACTED]") >= 3
    assert path == tmp_path / "diagnostics" / "project-open.log"


@pytest.mark.parametrize("phase", ["Đọc tệp dự án", "Nạp giao diện dự án"])
def test_open_reports_file_read_or_gui_hydration_error_separately(tmp_path, monkeypatch, phase):
    from tkinter import filedialog

    repo = service(tmp_path)
    state = repo.create_project("Existing")
    app = StudioApp.__new__(StudioApp)
    app.state = state
    app.root = object()
    app.service = repo
    app._confirm_project_navigation = lambda: True
    shown = []
    app._fail = shown.append
    monkeypatch.setattr(filedialog, "askdirectory", lambda *a, **k: str(state.directory))
    if phase == "Đọc tệp dự án":
        app.service = SimpleNamespace(config_path=repo.config_path,
            open_project=lambda path: (_ for _ in ()).throw(ValueError("bad project")))
    else:
        app._load_state = lambda new: (_ for _ in ()).throw(IndexError("Index -1 out of range"))
    app._open_project()
    assert phase in shown[0]
    assert str(tmp_path / "diagnostics" / "project-open.log") in shown[0]
    assert app.state is state
    assert phase in (tmp_path / "diagnostics" / "project-open.log").read_text(encoding="utf-8")
