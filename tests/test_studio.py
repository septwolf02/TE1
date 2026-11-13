"""Phase 1 editor persistence without a display or personal media."""
import json
import os
from pathlib import Path

import pytest

from te.project import ProjectManager, SectionName
from te.studio import StudioService
from te.media import KeyPlacementService, MediaAssignmentService
from te.project.models import AssignmentType


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
    state.subtitle_size = 174
    state.font_path = str(tmp_path / "NotoSansCJKjp-Regular.otf")
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
    assert reopened.subtitle_size == 174
    assert reopened.font_path == state.font_path
    assert len(reopened.project.segments) == 4 and len(reopened.project.sentence_units) >= 5
    service.rename_project(reopened, "野球 - tên mới")
    duplicated = service.duplicate_project(reopened, name="Bản sao Nhật Việt")
    assert duplicated.project.id != original_id
    assert duplicated.subtitle_size == 174
    assert duplicated.sections == reopened.sections
    assert duplicated.project.audio_records == []
    assert service.open_project(duplicated.directory).project.name == "Bản sao Nhật Việt"
    assert service.open_project(reopened.directory).project.name == "野球 - tên mới"
    assert not (duplicated.directory / "generated").exists()


def test_old_studio_sidecar_gets_large_subtitle_default(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Legacy captions")
    sidecar = state.directory / "studio.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data.pop("subtitle_size")
    sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert service.open_project(state.directory).subtitle_size == 156


def test_unique_project_seeds_and_manual_ordinary_reshuffle(tmp_path):
    from PIL import Image
    service = make_service(tmp_path)
    library = tmp_path / "shared-images"
    library.mkdir()
    for index in range(6):
        Image.new("RGB", (80, 45), (index * 35, 90, 110)).save(library / f"image-{index}.png")
    first = service.create_project("First")
    second = service.create_project("Second")
    assert first.project.random_seed != second.project.random_seed
    assert service.open_project(first.directory).project.random_seed == first.project.random_seed
    assert service.open_project(second.directory).project.random_seed == second.project.random_seed
    for state in (first, second):
        for section in SectionName:
            service.set_section(state, section, japanese="。".join(f"第{i}の場面" for i in range(4)) + "。", vietnamese="")
        service.save_project(state)
        service.select_media_folder(state, library)
        KeyPlacementService.assign_random_images(state.project, state.directory)
    def filenames(state):
        media = {item.id: item.filename for item in state.project.media}
        return tuple(media[item.media_id] for item in state.project.media_assignments)
    if filenames(first) == filenames(second):
        third = service.create_project("Third")
        for section in SectionName:
            service.set_section(third, section, japanese="。".join(f"第{i}の場面" for i in range(4)) + "。", vietnamese="")
        service.save_project(third)
        service.select_media_folder(third, library)
        KeyPlacementService.assign_random_images(third.project, third.directory)
        assert filenames(first) != filenames(third)
    old = {item.segment_id: item.media_id for item in first.project.media_assignments}
    locked_id = first.project.segments[0].id
    first.project.media_assignments[0].locked = True
    manual_id = first.project.segments[1].id
    first.project.media_assignments[1].assignment_type = AssignmentType.MANUAL
    service.save_project(first)
    seed = first.project.random_seed
    assert service.reshuffle_images(first) == len(first.project.segments) - 2
    assert first.project.random_seed != seed
    assignments = {item.segment_id: item.media_id for item in first.project.media_assignments}
    assert assignments[locked_id] == old[locked_id]
    assert assignments[manual_id] == old[manual_id]
    assert any(assignments[key] != old[key] for key in old if key not in {locked_id, manual_id})
    assert service.open_project(first.directory).project.random_seed == first.project.random_seed
    assert {item.segment_id: item.media_id for item in service.open_project(first.directory).project.media_assignments} == assignments
    KeyPlacementService.assign_random_images(first.project, first.directory)
    assert {item.segment_id: item.media_id for item in first.project.media_assignments} == assignments
    assert all(a != b for a, b in zip(
        [item.media_id for item in first.project.media_assignments],
        [item.media_id for item in first.project.media_assignments][1:]))


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


def test_clear_ordinary_folder_preserves_key_intro_and_source_files(tmp_path, monkeypatch):
    from PIL import Image
    import te.studio.service as studio_service
    from te.project.models import MediaCategory
    service = make_service(tmp_path)
    state = service.create_project("Clear folder")
    service.set_section(state, SectionName.P1, japanese="一。", vietnamese="Một.")
    service.save_project(state)
    folder = tmp_path / "ordinary"
    folder.mkdir()
    normal = folder / "normal.png"
    key = folder / "key-photo.png"
    Image.new("RGB", (80, 45)).save(normal)
    Image.new("RGB", (80, 45)).save(key)
    intro = tmp_path / "P1.mp4"
    intro.write_bytes(b"fixture")
    monkeypatch.setattr(studio_service, "_video_metadata", lambda _path: (1920, 1080, 3.0))
    service.set_intro(state, SectionName.P1, intro)
    service.select_media_folder(state, folder)
    draft = service.add_key_draft(state, key)
    ordinary = next(asset for asset in state.project.media if asset.path == str(normal))
    MediaAssignmentService().assign_media(state.project, state.project.segments[0].id,
                                          ordinary.id, locked=True)
    service.save_project(state)
    assert service.clear_media_folder(state) == 1
    reopened = service.open_project(state.directory)
    assert reopened.project.media_library_path is None
    assert reopened.project.media_assignments == []
    assert any(asset.id == draft["media_id"] and asset.category == MediaCategory.KEY
               for asset in reopened.project.media)
    assert len(reopened.key_drafts) == 1
    assert service.intro_path(reopened, SectionName.P1) == str(intro)
    assert normal.exists() and key.exists() and intro.exists()


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
    app.subtitle_chars.set("18")
    app.subtitle_size.set("180")
    pending = [app._details["media"]]
    button_labels = []
    while pending:
        widget = pending.pop()
        if isinstance(widget, ttk.Button):
            button_labels.append(widget.cget("text"))
        pending.extend(widget.winfo_children())
    assert "Xáo trộn lại ảnh" in button_labels
    main_labels = []
    pending = [app.body]
    while pending:
        widget = pending.pop()
        if isinstance(widget, ttk.Button):
            main_labels.append(widget.cget("text"))
            assert widget.cget("command")
        pending.extend(widget.winfo_children())
    assert "AI phân vai" in main_labels
    assert "Cài đặt AI" in main_labels
    assert "Xem nhanh 960×540" in main_labels
    assert "Xuất Full HD 1920×1080" in main_labels
    assert main_labels.count("Chi tiết / Cài đặt") == 6
    assert root.minsize() == (920, 690)
    assert set(app._details) == {"ai", "management", "keys", "media", "intros",
                                 "voice", "subtitles", "output"}
    for key in app._details:
        app._show_detail(key)
        root.update_idletasks()
        assert app._details[key].winfo_viewable()
        app._close_detail(key)
        assert not app._details[key].winfo_viewable()
    root.geometry("1100x750")
    root.update()
    assert app.vn_text.winfo_width() > 200 and app.jp_text.winfo_width() > 200
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
    draft_id = app.state.key_drafts[0]["id"]
    app._open_key_entry("draft", draft_id)
    panel = root.winfo_children()[-1].winfo_children()[0]
    combos = [child for child in panel.winfo_children() if isinstance(child, ttk.Combobox)]
    assert len(combos) == 3
    combos[0].set("P2")
    combos[1].set(combos[1]["values"][0])
    assert "Phần thứ hai." in combos[1].get()
    assert "次の部分です。" in combos[2].get()
    monkeypatch.setattr(messagebox, "askyesno", lambda *args, **kwargs: True)
    confirm = next(child for child in panel.winfo_children()
                   if isinstance(child, ttk.Button) and child["text"] == "Xác nhận VN–JP")
    confirm.invoke()
    assert len(app.state.project.key_placements) == 1
    app._switch_section(SectionName.P1)
    app.jp_text.insert("end", "もう一文。")
    app._script_changed()
    assert app._save()
    second_key = tmp_path / "ảnh KEY thứ hai.png"
    second_key.write_bytes(b"second test image")
    app.service.add_key_draft(app.state, second_key)
    app._refresh_key_rows()
    draft_id = app.state.key_drafts[0]["id"]
    app._open_key_entry("draft", draft_id)
    draft_panel = root.winfo_children()[-1].winfo_children()[0]
    draft_combos = [child for child in draft_panel.winfo_children() if isinstance(child, ttk.Combobox)]
    draft_combos[1].set(draft_combos[1]["values"][0])
    assert not draft_combos[2].get()
    draft_combos[2].set(draft_combos[2]["values"][1])
    second_confirm = next(child for child in draft_panel.winfo_children()
                          if isinstance(child, ttk.Button) and child["text"] == "Xác nhận VN–JP")
    second_confirm.invoke()
    assert len(app.state.project.key_placements) == 2
    app.speed.set("1.2")
    app.narration_mode.set("Nhiều giọng A/B/C/D")
    app.pitch.set("0.05")
    app.subtitle_color.set("#FFEEDD")
    app.motion.set("Chỉ pan")
    app.intro_enabled[SectionName.P2].set(False)
    app._set_intro_preference(SectionName.P2, False)
    assert app._save()
    assert "1.2×" in app.voice_summary.get()
    assert "#FFEEDD" in app.subtitle_summary.get()
    assert "Chỉ pan" in app.output_summary.get()
    app._load_state(app.service.open_project(path))
    assert app.speed.get() == "1.2"
    assert app.narration_mode.get() == "Nhiều giọng A/B/C/D"
    assert app.subtitle_color.get() == "#FFEEDD"
    assert app.intro_enabled[SectionName.P2].get() is False
    root.destroy()
    reopened = app.service.open_project(path)
    assert reopened.sections["P1"]["ja"] == "初めての日本語。もう一文。"
    assert reopened.project.subtitle_style["max_chars_per_line"] == 18
    assert reopened.subtitle_size == 180
    assert reopened.sections["P2"]["vi"] == "Phần thứ hai."
    assert len(reopened.project.key_placements) == 2
    assert reopened.project.tts_settings.speaking_rate == 1.2
    assert reopened.narration_mode == "multi"
    assert reopened.project.tts_settings.pitch == 0.05
    assert reopened.project.subtitle_style["text_color"] == "#FFEEDD"
    assert reopened.motion_preset == "pan"


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
def test_tk_render_worker_keeps_editor_responsive_and_reports_errors(tmp_path, monkeypatch):
    import time
    import tkinter as tk
    from tkinter import simpledialog
    from te.studio.gui import StudioApp

    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "GUI render worker")
    root = tk.Tk()
    app = StudioApp(root, make_service(tmp_path))
    errors = []
    monkeypatch.setattr(app, "_fail", errors.append)
    ticked = []
    root.after(30, lambda: ticked.append(True))
    def slow_job(_state, progress):
        time.sleep(0.15)
        progress("Đã xử lý bước thử")
        return "xong"
    app._start_job("Bắt đầu thử", slow_job)
    assert app._job_running
    deadline = time.monotonic() + 3
    while app._job_running and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert ticked and not app._job_running
    assert "Đã xử lý bước thử" in app.render_log.get("1.0", "end")
    app._start_job("Lỗi thử", lambda _state, _progress: (_ for _ in ()).throw(ValueError("Intro P1 bị thiếu")))
    deadline = time.monotonic() + 3
    while app._job_running and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert errors == ["Intro P1 bị thiếu"] and not app._job_running
    root.destroy()
