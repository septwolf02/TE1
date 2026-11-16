"""Project navigation recovery without requiring a desktop display."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from te.studio.gui import SECTIONS, StudioApp
from te.studio.service import StudioService


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def service(tmp_path):
    result = StudioService(tmp_path / "settings.json")
    result.set_directories(tmp_path / "projects", tmp_path / "output")
    return result


def navigation_app(current, saves):
    app = StudioApp.__new__(StudioApp)
    app.state = current
    app.root = object()
    app.channel = Var()
    app._save = lambda *, auto=False: saves.pop(0)
    app._load_state = lambda state: setattr(app, "state", state)
    return app


@pytest.mark.parametrize("choice,opens", [(False, True), (None, False), (True, False)])
def test_open_with_blank_current_name_offers_discard_retry_cancel(tmp_path, monkeypatch, choice, opens):
    from tkinter import filedialog, messagebox

    repo = service(tmp_path)
    current = repo.create_project("Current")
    target = repo.create_project("Target")
    app = navigation_app(current, [False] + ([False] if choice is True else []))
    selected = []
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: choice)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(filedialog, "askdirectory", lambda *a, **k: selected.append(True) or str(target.directory))
    app.service = repo
    app._fail = lambda message: None

    app._open_project()

    assert bool(selected) is opens
    assert app.state.project.name == ("Target" if opens else "Current")
    assert repo.open_project(current.directory).project.name == "Current"
    assert repo.open_project(target.directory).project.name == "Target"


def test_valid_unsaved_project_saves_before_folder_picker(tmp_path, monkeypatch):
    from tkinter import filedialog, messagebox

    repo = service(tmp_path)
    current = repo.create_project("Current")
    target = repo.create_project("Target")
    events = []
    app = navigation_app(current, [])
    app.service = repo
    app._save = lambda *, auto=False: events.append("save") or True
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: pytest.fail("Unexpected prompt"))
    monkeypatch.setattr(filedialog, "askdirectory", lambda *a, **k: events.append("picker") or str(target.directory))
    app._open_project()
    assert events == ["save", "picker"]
    assert app.state.project.name == "Target"


def test_create_can_discard_failed_save_but_cancel_keeps_current(tmp_path, monkeypatch):
    from tkinter import messagebox, simpledialog

    repo = service(tmp_path)
    current = repo.create_project("Current")
    app = navigation_app(current, [False, False])
    app.service = repo
    app._fail = lambda message: pytest.fail(message)
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "New")
    answers = iter((None, False))
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: next(answers))
    app._new_project()
    assert app.state.project.name == "Current"
    app._new_project()
    assert app.state.project.name == "New"
    assert repo.open_project(current.directory).project.name == "Current"


def test_create_rejects_blank_name_without_touching_current_project(tmp_path, monkeypatch):
    from tkinter import simpledialog

    repo = service(tmp_path)
    current = repo.create_project("Current")
    app = navigation_app(current, [])
    app.service = repo
    errors = []
    app._fail = errors.append
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "  ")
    app._new_project()
    assert errors == ["Tên video không được để trống."]
    assert app.state.project.name == "Current"
    assert repo.open_project(current.directory).project.name == "Current"


def test_blank_name_save_does_not_mutate_project_or_disk(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("Original")
    app = StudioApp.__new__(StudioApp)
    app.state = state
    app.video_name = Var("")
    app._capture_section = lambda: None
    app._save_after = None
    app._save_status = Var()
    app.publisher = SimpleNamespace(capture=lambda: None)
    app.service = repo
    assert not app._save(auto=True)
    assert "Tên video" in app._save_status.get()
    assert state.project.name == "Original"
    assert repo.open_project(state.directory).project.name == "Original"


def test_open_project_restores_saved_name_for_gui_loading(tmp_path):
    repo = service(tmp_path)
    state = repo.create_project("Tên dự án 日本語")
    state.sections["P1"]["ja"] = "本文。"
    repo.save_project(state)
    reloaded = repo.open_project(state.directory)
    assert reloaded.project.name == "Tên dự án 日本語"
    assert reloaded.sections["P1"]["ja"] == "本文。"

    class LoadHarness:
        def __init__(self):
            self.service = repo
            self.video_name = Var()
            self.intro_paths = {section: Var() for section in SECTIONS}
            self.intro_enabled = {section: Var() for section in self.intro_paths}
            self._images = []
            self.publisher = SimpleNamespace(load=lambda state: None)
            self.ai = SimpleNamespace(project_loaded=lambda: None)
            self._save_status = Var()

        def __getattr__(self, key):
            if key.startswith("_"):
                return lambda *args: None
            value = Var()
            setattr(self, key, value)
            return value

    gui = LoadHarness()
    StudioApp._load_state(gui, reloaded)
    assert gui.video_name.get() == "Tên dự án 日本語"
    assert gui.state.directory == Path(state.directory)
