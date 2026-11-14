"""New-project defaults and safe automatic B1 confirmation without live services."""
from copy import deepcopy
import os
from types import SimpleNamespace

from PIL import Image
import pytest

from te.project.models import SectionName
from te.studio.ai_gui import SpeakerAIController
from te.studio.ai_speakers import resolve_japanese_references, segment_passages
from te.studio.multi_voice import configuration_signature, speech_plan
from te.studio.render_queue import RenderQueue
from te.studio.service import StudioService


def prepared(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("B1 automatic")
    service.set_section(state, SectionName.P1, vietnamese="MC. Người kể.", japanese="案内。発言。")
    service.save_project(state)
    passages = [item for item in segment_passages(state.sections).values()
                if item["language"] == "ja"]
    rows, warnings = resolve_japanese_references([
        {"japanese_id": item["id"], "role": role, "confidence": "high", "warning": ""}
        for item, role in zip(passages, ("A", "B"))], state.sections)
    assert not warnings
    return service, state, rows


def controller(service, state):
    messages = []
    ai = SpeakerAIController.__new__(SpeakerAIController)
    ai.app = SimpleNamespace(service=service, state=state,
                             _refresh_compact_summaries=lambda: messages.append("main"))
    ai.assignment_status = SimpleNamespace(set=messages.append)
    return ai, messages


def test_new_multi_default_preserves_existing_single_and_machine_voices(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    old = service.create_project("Old single")
    assert old.narration_mode == "multi" and old.speaker_data["voices"] == {}
    old.narration_mode = "single"
    service.save_project(old)
    old_voices = deepcopy(old.speaker_data)
    source = service.create_project("Preset source")
    source.speaker_data["voices"] = {role: {"speaker_uuid": role, "style_id": index,
        "speaker": role, "style": "Normal"} for index, role in enumerate("ABCD", 1)}
    service.save_default_role_voices(source)
    new = StudioService(service.config_path).create_project("New multi")
    assert new.narration_mode == "multi"
    assert set(new.speaker_data["voices"]) == set("ABCD")
    assert service.open_project(new.directory).narration_mode == "multi"
    assert service.open_project(old.directory).narration_mode == "single"
    assert service.open_project(old.directory).speaker_data == old_voices
    (old.directory / "studio.json").unlink()  # Simulate a project predating the sidecar.
    assert service.open_project(old.directory).narration_mode == "single"


def test_gpt_result_auto_confirms_persists_and_preserves_voices(tmp_path):
    service, state, rows = prepared(tmp_path)
    state.speaker_data["voices"]["B"] = {"speaker_uuid": "uuid-B", "style_id": 2,
                                           "speaker": "B", "style": "Normal"}
    state.speaker_data["parameters"]["B"] = {"speaking_rate": 1.2, "pitch": 0.02,
                                                  "intonation": 1.1, "volume": 0.9}
    service.save_project(state)
    old_voices = deepcopy(state.speaker_data["voices"])
    old_parameters = deepcopy(state.speaker_data["parameters"])
    ai, messages = controller(service, state)
    result = ai._save_confirmed_assignments(state, rows, state.speaker_data["identities"],
                                            deepcopy(state.sections))
    reopened = service.open_project(state.directory)
    assert result["status"] == reopened.speaker_data["status"] == "confirmed"
    assert all(item["confirmed"] for item in reopened.speaker_data["assignments"])
    assert reopened.speaker_data["voices"] == old_voices
    assert reopened.speaker_data["parameters"] == old_parameters
    assert not speech_plan(reopened)[1]
    assert "2 đoạn • confirmed" in messages and "main" in messages


def test_stale_incomplete_overlap_and_failed_save_keep_previous_confirmation(tmp_path, monkeypatch):
    service, state, rows = prepared(tmp_path)
    ai, _ = controller(service, state)
    source = deepcopy(state.sections)
    ai._save_confirmed_assignments(state, rows, state.speaker_data["identities"], source)
    original = deepcopy(state.speaker_data)
    with pytest.raises(ValueError, match="Script đã đổi"):
        ai._save_confirmed_assignments(state, rows, original["identities"],
                                       {**source, "P1": {"vi": source["P1"]["vi"], "ja": "別の話。"}})
    with pytest.raises(ValueError, match="chưa phân vai"):
        ai._save_confirmed_assignments(state, rows[:1], original["identities"], source)
    with pytest.raises(ValueError, match="chồng lấn"):
        ai._save_confirmed_assignments(state,
            [rows[0], dict(rows[1], japanese=rows[0]["japanese"])], original["identities"], source)
    assert state.speaker_data == original
    replacement = deepcopy(rows)
    replacement[1]["role"] = "C"
    import te.studio.service as studio_service
    monkeypatch.setattr(studio_service, "_write_json", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        ai._save_confirmed_assignments(state, replacement, original["identities"], source)
    assert state.speaker_data == original
    assert StudioService(service.config_path).open_project(state.directory).speaker_data == original


def test_manual_role_save_changes_only_affected_b2_assignment(tmp_path):
    service, state, rows = prepared(tmp_path)
    ai, _ = controller(service, state)
    source = deepcopy(state.sections)
    ai._save_confirmed_assignments(state, rows, state.speaker_data["identities"], source)
    before = configuration_signature(state)
    old_first = deepcopy(state.speaker_data["assignments"][0])
    correction = deepcopy(state.speaker_data["assignments"])
    correction[1]["role"] = "C"
    correction[1]["source"] = "manual"
    result = ai._save_confirmed_assignments(state, correction, state.speaker_data["identities"], source)
    assert result["status"] == "confirmed"
    assert result["assignments"][0] == old_first
    assert result["assignments"][1]["role"] == "C"
    assert result["assignments"][1]["source"] == "manual"
    assert configuration_signature(state) != before
    reopened = service.open_project(state.directory)
    assert any(role == "C" for parts in speech_plan(reopened)[0].values() for role, _ in parts)


def test_queue_snapshot_keeps_confirmed_roles_after_manual_correction(tmp_path):
    service, state, rows = prepared(tmp_path)
    ai, _ = controller(service, state)
    ai._save_confirmed_assignments(state, rows, state.speaker_data["identities"], deepcopy(state.sections))
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (320, 180), "blue").save(image_dir / "one.png")
    service.select_media_folder(state, image_dir)
    queue = RenderQueue(service)
    job = queue.enqueue(state)
    corrected = deepcopy(state.speaker_data["assignments"])
    corrected[1]["role"] = "C"
    corrected[1]["source"] = "manual"
    ai._save_confirmed_assignments(state, corrected, state.speaker_data["identities"],
                                   deepcopy(state.sections))
    frozen = service.open_project(job["snapshot_path"])
    assert frozen.speaker_data["assignments"][1]["role"] == "B"
    assert state.speaker_data["assignments"][1]["role"] == "C"
    assert queue.jobs()[0]["status"] == "queued"


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
def test_script_editors_start_compact_and_expand_without_losing_text(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import simpledialog, ttk
    from te.studio.gui import StudioApp

    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "Compact")
    root = tk.Tk()
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    app = StudioApp(root, service)
    root.update()
    assert app.narration_mode.get() == "Nhiều giọng A/B/C/D"
    assert app.vn_text.cget("height") == app.jp_text.cget("height") == 6
    app.vn_text.insert("1.0", "Một đoạn Việt.\nĐoạn tiếp theo.")
    pending = [app.body]
    toggle = None
    below = None
    while pending:
        widget = pending.pop()
        if isinstance(widget, ttk.Button) and widget.cget("text") == "Mở rộng ô kịch bản":
            toggle = widget
        if isinstance(widget, ttk.Button) and widget.cget("text") == "AI phân vai":
            below = widget
        pending.extend(widget.winfo_children())
    assert toggle is not None and below is not None
    compact_height = app.vn_text.winfo_height()
    compact_below = below.winfo_rooty()
    script_host = app.vn_text.master.master.master
    assert int(script_host.pack_info()["expand"]) == 0
    assert script_host.winfo_height() < 280
    toggle.invoke()
    root.update()
    assert app.vn_text.cget("height") == app.jp_text.cget("height") == 12
    assert app.vn_text.winfo_height() > compact_height + 50
    assert below.winfo_rooty() > compact_below + 50
    toggle.invoke()
    root.update()
    assert app.vn_text.cget("height") == app.jp_text.cget("height") == 6
    assert abs(below.winfo_rooty() - compact_below) < 5
    root.geometry("1100x750")
    root.update()
    assert app.vn_text.winfo_height() >= compact_height - 5
    root.geometry("920x690")
    root.update()
    assert app.vn_text.winfo_width() > 200 and app.jp_text.winfo_width() > 200
    assert "Đoạn tiếp theo." in app.vn_text.get("1.0", "end")
    app.subtitle_background_enabled.set(False)
    app.subtitle_background_opacity.set(25)
    app.headline_background_opacity.set(60)
    assert app.subtitle_background_percent.get() == "25%"
    assert app.headline_background_percent.get() == "60%"
    assert app._save()
    reopened = service.open_project(app.state.directory)
    assert reopened.project.subtitle_style["background_enabled"] is False
    assert reopened.project.subtitle_style["background_opacity"] == 25
    assert reopened.project.subtitle_style["headline_background_opacity"] == 60
    pending = [app._details["subtitles"]]
    while pending:
        widget = pending.pop()
        if isinstance(widget, ttk.Button) and widget.cget("text") == "Vừa":
            widget.invoke()
            break
        pending.extend(widget.winfo_children())
    assert app.subtitle_background_opacity.get() == 40
    assert app._save()
    assert service.open_project(app.state.directory).project.subtitle_style["background_opacity"] == 40
    root.destroy()
