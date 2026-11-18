"""Project loading safety and diagnostics without a Tk display."""
import json
import os
from queue import Queue
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


class Tree:
    def __init__(self):
        self.rows = {}
        self.selected = ()

    def get_children(self):
        return tuple(self.rows)

    def delete(self, iid):
        self.rows.pop(iid)

    def insert(self, parent, where, *, iid, values):
        self.rows[iid] = values

    def selection(self):
        return self.selected

    def selection_set(self, iid):
        self.selected = (iid,)

    def selection_remove(self, *items):
        self.selected = ()

    def see(self, iid):
        pass


class Text:
    def delete(self, *args):
        self.value = ""

    def insert(self, *args):
        self.value = args[-1]

    def get(self, *args):
        return getattr(self, "value", "")


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
    controller.channel_tree = Tree()
    controller.manual_channel_id = Var()
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
    assert controller.channel_tree.selection() == ()
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller.channel_name.get() == ""
    assert controller.store.authorized_channels() == []

    controller.store.add_verified_channel(CHANNEL, "Kênh cũ")
    controller.load(restored)
    assert controller.channel_tree.selection() == (f"api:{CHANNEL}",)
    assert controller.channel_name.get() == "Kênh cũ"
    controller.channel_records = []
    controller.channel_tree.selection_remove()
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
    assert controller.channel_tree.rows == {}
    assert controller.channel_tree.selection() == ()
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
    controller.channel_tree.selection_set("studio:1")
    controller._select_channel()
    assert controller.values["connection_type"].get() == "studio_connector"
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller._selected_connection() is None
    controller.channel_tree.selection_set(f"api:{CHANNEL}")
    controller._select_channel()
    assert controller.values["connection_type"].get() == "youtube_api"
    assert controller._selected_connection()["id"] == CHANNEL
    controller.channel_tree.selection_remove()
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
    controller.channel_tree.selection_set("studio:0")
    controller._select_channel()
    assert controller.values["channel_id"].get() == CHANNEL
    assert controller.values["connection_type"].get() == "studio_connector"
    assert controller._selected_connection() is None


def test_channel_table_17_rows_manual_id_and_studio_draft(tmp_path):
    from te.studio.youtube_api import SCOPES

    repo = service(tmp_path)
    state = repo.create_project("Channel table")
    controller = publisher_harness(repo, state)
    controller.store.register_authorized_channel({"id": CHANNEL, "name": "API Owner",
        "credential_ref": "a" * 32, "scopes": list(SCOPES), "authorization_status": "authorized"})
    records = []
    for number in range(17):
        name = f"日本語の長いチャンネル名と説明 {number} " + "日本語" * 30
        records.append({"key":f"candidate:{number}", "id":"", "handle":f"@channel{number}",
            "discovered_name":name, "display_name":name, "name_source":"studio_switcher_dom",
            "handle_source":"studio_switcher_dom", "id_source":"missing", "status":"discovered",
            "permission_status":"permission_unverified", "upload_status":"upload_unverified",
            "source_url":"https://studio.youtube.com/", "evidence":"visible_switcher_item",
            "user_edited":False})
    controller.studio_store.save_catalog({"type":"catalog_sync", "schema":1, "records":records})
    controller.refresh_channels()
    assert len(controller.channel_tree.rows) == 18
    assert controller.channel_tree.rows["studio:candidate:5"][0] == records[5]["display_name"]
    controller.channel_tree.selection_set("studio:candidate:5")
    controller._select_channel()
    assert controller.values["studio_catalog_key"].get() == "candidate:5"
    assert controller.values["channel_id"].get() == ""
    assert controller._selected_connection() is None

    manual_id = "UC" + "b" * 22
    controller.manual_channel_id.set(manual_id)
    controller.link_manual_id()
    assert controller.channel_tree.selection() == ("studio:candidate:5",)
    assert controller.values["channel_id"].get() == manual_id
    assert controller.studio_store.catalog_records()[5]["id_source"] == "user_supplied"
    video = tmp_path / "full-hd.mp4"
    thumbnail = tmp_path / "thumbnail.png"
    video.write_bytes(b"MP4 data")
    thumbnail.write_bytes(b"PNG data")
    controller.values["video_path"].set(str(video))
    controller.values["thumbnail_path"].set(str(thumbnail))
    controller.values["title"].set("大谷翔平の最新ニュース")
    controller.values["tags"].set("大谷翔平,野球")
    controller.description.value = "日本語の説明"
    controller.app.state = state
    controller.app._save = lambda: (repo.save_project(state) or True)
    controller.save_studio_draft()
    saved = PublisherStore(repo.config_path).latest(state.project.id)
    assert saved["status"] == "draft" and saved["metadata"]["connection_type"] == "studio_connector"
    assert saved["channel_id"] == manual_id and saved["credential_ref"] == ""
    assert saved["source"]["path"] == str(video.resolve())
    assert saved["metadata"]["description"] == "日本語の説明"
    assert len(StudioConnectorStore(repo.config_path).catalog_records()) == 17
    reopened = repo.open_project(state.directory)
    assert reopened.publisher["studio_catalog_key"] == "candidate:5"
    assert reopened.publisher["channel_id"] == manual_id
    with pytest.raises(ValueError, match="chưa hỗ trợ upload"):
        controller.store.prepare(state.project.id, reopened.publisher, {})

    controller.channel_tree.selection_set(f"api:{CHANNEL}")
    controller._select_channel()
    assert controller._selected_connection()["id"] == CHANNEL


def test_publisher_selection_request_updates_only_selected_studio_channel(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("Verify selected")
    controller = publisher_harness(repo, state)
    first = {"key":"candidate:first", "id":"", "handle":"@first",
             "discovered_name":"同じ名前", "display_name":"同じ名前",
             "name_source":"studio_switcher_dom", "handle_source":"studio_switcher_dom",
             "id_source":"missing", "status":"discovered",
             "permission_status":"permission_unverified", "upload_status":"upload_unverified",
             "source_url":"https://studio.youtube.com/", "evidence":"visible_switcher_item",
             "user_edited":False}
    second = {**first, "key":"candidate:second", "handle":"@second"}
    controller.studio_store.save_catalog({"type":"catalog_sync", "schema":1,
                                          "records":[first, second]})
    controller.refresh_channels()
    controller.channel_tree.selection_set("studio:candidate:second")
    controller._select_channel()
    controller.open_studio_verification()
    request = controller.studio_store.selection_request()
    assert request["key"] == "candidate:second"
    channel_id = "UC" + "b" * 22
    studio_url = f"https://studio.youtube.com/channel/{channel_id}/videos"
    controller.studio_store.confirm_selection(request["request_id"], studio_url,
                                              studio_url, "Studio B")
    controller.events = Queue()
    controller.app.root = SimpleNamespace(winfo_exists=lambda: False)
    controller.poll()
    assert controller.values["channel_id"].get() == channel_id
    assert controller._selected_connection() is None
    assert controller.studio_store.catalog_records()[0]["id"] == ""
    assert controller.studio_store.catalog_records()[1]["id"] == channel_id


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
@pytest.mark.parametrize("scaling", [1.25, 1.5])
def test_publisher_channel_table_scrolls_at_display_scaling(tmp_path, scaling):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * scaling)
    try:
        app = StudioApp(root, service(tmp_path))
        dialog = app._details["publisher"]
        dialog.deiconify()
        root.update()
        tree = app.publisher.channel_tree
        assert tree.cget("show") == "headings"
        assert tree.cget("height") >= 10
        assert set(tree["columns"]) == {"name", "handle", "id", "connection", "identity", "authorization"}
        table = tree.master
        assert len([child for child in table.winfo_children() if isinstance(child, ttk.Scrollbar)]) == 2
        canvases = [child for child in dialog.winfo_children()[0].winfo_children()
                    if isinstance(child, tk.Canvas)]
        assert canvases and canvases[0].yview()[1] < 1
    finally:
        root.destroy()


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
