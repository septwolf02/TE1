"""Phase 1 editor persistence without a display or personal media."""
import json
import os
from pathlib import Path

import pytest

from te.project import ProjectManager, SectionName
from te.studio import StudioService


def make_service(tmp_path):
    service = StudioService(tmp_path / "app-settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    return service


def test_create_open_save_rename_duplicate_unicode_and_sections(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("大谷 – câu chuyện", channel=None)
    original_id = state.project.id
    texts = {
        "P1": ("Đoạn Việt đầu.\n\nVẫn giữ xuống dòng.\n", "最初の文。\n\n次の文！\n"),
        "P2": ("Hai câu chuyện.", "第二部です。"),
        "P3": ("Tham chiếu riêng.", "第三部です。"),
        "P4": ("Kết thúc.", "最後です。"),
    }
    for section in SectionName:
        vi, ja = texts[section.value]
        service.set_section(state, section, japanese=ja, vietnamese=vi)
    state.motion_preset = "pan"
    state.transition = "fade"
    state.image_min_seconds = 8
    state.image_max_seconds = 11
    state.project.random_seed = 1234
    state.project.tts_settings.speaking_rate = 1.15
    state.project.tts_settings.voice_id = "剣崎雌雄"
    state.project.tts_settings.style = "ノーマル"
    state.project.tts_settings.pitch = 0.03
    state.project.tts_settings.intonation = 1.2
    state.project.tts_settings.volume = 0.9
    state.project.subtitle_style["text_color"] = "#FFEEDD"
    service.save_project(state)
    reopened = service.open_project(state.directory)
    assert reopened.project.id == original_id
    assert reopened.sections == state.sections
    assert reopened.motion_preset == "pan" and reopened.transition == "fade"
    assert (reopened.image_min_seconds, reopened.image_max_seconds) == (8, 11)
    assert reopened.project.random_seed == 1234
    assert reopened.project.tts_settings.speaking_rate == 1.15
    assert (reopened.project.tts_settings.voice_id, reopened.project.tts_settings.style) == ("剣崎雌雄", "ノーマル")
    assert (reopened.project.tts_settings.pitch, reopened.project.tts_settings.intonation,
            reopened.project.tts_settings.volume) == (0.03, 1.2, 0.9)
    assert reopened.project.subtitle_style["text_color"] == "#FFEEDD"
    assert len(reopened.project.segments) == 4 and len(reopened.project.sentence_units) >= 5
    service.rename_project(reopened, "野球 - tên mới")
    duplicated = service.duplicate_project(reopened, name="Bản sao Nhật Việt")
    assert duplicated.project.id != original_id
    assert duplicated.sections == reopened.sections
    assert duplicated.project.audio_records == []
    assert service.open_project(duplicated.directory).project.name == "Bản sao Nhật Việt"
    assert service.open_project(reopened.directory).project.name == "野球 - tên mới"
    assert not (duplicated.directory / "generated").exists()


def test_channel_defaults_and_project_media_isolation(tmp_path):
    service = make_service(tmp_path)
    first = service.create_project("Video một")
    service.create_profile("Kênh thể thao", first)
    first.project.tts_settings.voice_id = "後鬼"
    first.project.tts_settings.speaking_rate = 1.2
    first.motion_preset = "zoom"
    first.intro_preferences["P2"] = False
    service.save_profile_from_project("Kênh thể thao", first)
    library = tmp_path / "images"
    library.mkdir()
    (library / "one.jpg").write_bytes(b"image")
    assert service.select_media_folder(first, library) == 1
    second = service.create_project("Video hai", channel="Kênh thể thao")
    assert second.channel == "Kênh thể thao"
    assert second.project.tts_settings.voice_id == "後鬼"
    assert second.project.tts_settings.speaking_rate == 1.2
    assert second.motion_preset == "zoom" and not second.intro_preferences["P2"]
    assert second.project.media_library_path is None and service.eligible_image_count(second) == 0
    service.rename_profile("Kênh thể thao", "Kênh mới")
    assert "Kênh mới" in StudioService(service.config_path).profile_names()
    assert StudioService(service.config_path).open_project(second.directory).channel == "Kênh mới"
    assert StudioService(service.config_path).settings["output_root"] == str(tmp_path / "output")


def test_key_drafts_confirmation_missing_media_and_intro_controls(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("KEY demo")
    service.set_section(state, SectionName.P1, japanese="一文目。二文目！", vietnamese="Câu Việt đầu.\nCâu Việt sau.")
    service.save_project(state)
    key1 = tmp_path / "ảnh KEY một.png"
    key2 = tmp_path / "ảnh KEY hai.jpg"
    key1.write_bytes(b"source one")
    key2.write_bytes(b"source two")
    original = key1.read_bytes()
    draft1 = service.add_key_draft(state, key1)
    draft2 = service.add_key_draft(state, key2)
    assert len(service.open_project(state.directory).key_drafts) == 2
    units = [unit for unit in state.project.sentence_units if unit.segment_id == state.project.segments[0].id]
    with pytest.raises(ValueError, match="không thuộc"):
        service.confirm_key_draft(state, draft1["id"], section=SectionName.P2,
            sentence_id=units[0].id, vietnamese_text="Câu Việt đầu.")
    placement = service.confirm_key_draft(state, draft1["id"], section=SectionName.P1,
        sentence_id=units[0].id, vietnamese_text="Câu Việt đầu.", offset_ms=-30)
    assert placement.duration_ms == 7000 and placement.offset_ms == -30
    assert len(state.key_drafts) == 1 and state.key_drafts[0]["id"] == draft2["id"]
    assert state.project.sentence_units[0].mapping_confirmed
    assert not state.project.sentence_units[1].mapping_confirmed
    assert key1.read_bytes() == original
    intro = tmp_path / "mở đầu P1.mp4"
    intro.write_bytes(b"intro source")
    service.set_intro(state, SectionName.P1, intro)
    assert service.intro_path(service.open_project(state.directory), SectionName.P1) == str(intro)
    service.set_intro(state, SectionName.P1, None)
    assert service.intro_path(state, SectionName.P1) is None and intro.read_bytes() == b"intro source"
    intro.unlink()
    key2.unlink()
    with pytest.warns(RuntimeWarning):
        reopened = service.open_project(state.directory)
    assert any(not asset.available for asset in reopened.project.media)
    assert len(reopened.key_drafts) == 1 and len(reopened.project.key_placements) == 1


def test_external_key_and_intro_survive_ordinary_folder_refresh(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Media paths")
    key = tmp_path / "external-key.jpg"
    intro = tmp_path / "external-intro.mp4"
    key.write_bytes(b"key")
    intro.write_bytes(b"intro")
    service.add_key_draft(state, key)
    service.set_intro(state, SectionName.P1, intro)
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    (ordinary / "a.jpg").write_bytes(b"normal")
    assert service.select_media_folder(state, ordinary) == 1
    reopened = service.open_project(state.directory)
    assert all(asset.available for asset in reopened.project.media)
    assert service.intro_path(reopened, SectionName.P1) == str(intro)
    assert key.read_bytes() == b"key"


def test_vietnamese_only_draft_section_survives_reopen(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Bản nháp Việt")
    service.set_section(state, SectionName.P3, japanese="", vietnamese="Chưa có bản Nhật.\n\nGiữ nguyên đoạn hai.")
    service.save_project(state)
    reopened = service.open_project(state.directory)
    assert reopened.sections["P3"]["vi"] == "Chưa có bản Nhật.\n\nGiữ nguyên đoạn hai."
    assert not any(segment.section == SectionName.P3 for segment in reopened.project.segments)


def test_legacy_v8_project_and_interrupted_sidecar_recovery(tmp_path):
    service = make_service(tmp_path)
    directory = tmp_path / "legacy"
    manager = ProjectManager()
    project = manager.create(directory, "Dự án cũ")
    first = manager.add_segment(project, SectionName.P1, 1, "一。", "Một.")
    second = manager.add_segment(project, SectionName.P1, 2, "二。", "Hai.")
    manager.save(directory, project)
    data = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 8
    state = service.open_project(directory)
    assert state.sections["P1"]["ja"] == "一。\n\n二。"
    service.save_project(state)
    assert [s.id for s in state.project.segments] == [first.id, second.id]
    service.set_section(state, SectionName.P1, japanese="改訂一。\n\n二。", vietnamese="Một.\n\nHai.")
    service.save_project(state)
    assert [s.id for s in state.project.segments] == [first.id, second.id]
    service.set_section(state, SectionName.P1, japanese="Đoạn không cùng số.", vietnamese="Một.")
    with pytest.raises(ValueError, match="giữ cùng số đoạn"):
        service.save_project(state)
    recovered = service.open_project(directory)
    assert recovered.sections["P1"]["ja"] == "改訂一。\n\n二。"
    sidecar = json.loads((directory / "studio.json").read_text(encoding="utf-8"))
    sidecar["sections"]["P1"]["ja"] = "Bản nháp lỗi"
    (directory / "studio.json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    project_data = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    project_data["name"] = "Tên sau khi lưu project"
    (directory / "project.json").write_text(json.dumps(project_data, ensure_ascii=False), encoding="utf-8")
    recovered = service.open_project(directory)
    assert recovered.recovery_warning and recovered.sections["P1"]["ja"] == "改訂一。\n\n二。"


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
def test_tk_gui_launch_autosave_and_reopen(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    from te.studio.gui import StudioApp

    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "GUI 日本語")
    root = tk.Tk()
    app = StudioApp(root, make_service(tmp_path))
    app.jp_text.insert("1.0", "初めての日本語。")
    app.vn_text.insert("1.0", "Lời Việt đầu tiên.")
    app._script_changed()
    app._switch_section(SectionName.P2)
    assert app.state.sections["P1"]["ja"] == "初めての日本語。"
    root.after(1100, root.quit)
    root.mainloop()
    assert app._save_status.get() == "Đã lưu"
    path = app.state.directory
    app.jp_text.insert("1.0", "次の部分です。")
    app.vn_text.insert("1.0", "Phần thứ hai.")
    app._script_changed()
    assert app._save()
    key_file = tmp_path / "ảnh tiết lộ.png"
    key_file.write_bytes(b"test image")
    app.service.add_key_draft(app.state, key_file)
    app._refresh_key_rows()
    panel = app.key_rows.winfo_children()[0]
    combos = [child for child in panel.winfo_children() if isinstance(child, ttk.Combobox)]
    assert len(combos) == 2
    combos[1].set(combos[1]["values"][0])
    vn_field = next(child for child in panel.winfo_children()
                    if isinstance(child, ttk.Entry) and child.grid_info()["row"] == 1)
    vn_field.insert(0, "Câu Việt xác nhận.")
    monkeypatch.setattr(messagebox, "askyesno", lambda *args, **kwargs: True)
    confirm = next(child for child in panel.winfo_children()
                   if isinstance(child, ttk.Button) and child["text"] == "Xác nhận VN–JP")
    confirm.invoke()
    assert len(app.state.project.key_placements) == 1
    root.destroy()
    reopened = app.service.open_project(path)
    assert reopened.sections["P1"]["ja"] == "初めての日本語。"
    assert reopened.sections["P2"]["vi"] == "Phần thứ hai."
    assert len(reopened.project.key_placements) == 1
