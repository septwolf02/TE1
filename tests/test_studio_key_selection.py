"""Japanese sentence IDs alone control KEY placement; Vietnamese is optional context."""
import inspect
import os

import pytest

from te.project import SectionName
from te.media import KeyPlacementService
from te.studio.gui import StudioApp, _key_sentence_label
from te.studio.service import StudioService, split_vietnamese_units


def setup(tmp_path, japanese, vietnamese):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("KEY selection")
    service.set_section(state, SectionName.P1, japanese=japanese, vietnamese=vietnamese)
    service.save_project(state)
    return service, state


def key_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"source media")
    return path


def test_vietnamese_units_and_paragraph_suggestions(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目！\n\n三文目？", "Câu đầu. Câu thứ hai!\n\nCâu cuối?")
    units = service.vietnamese_units(state, SectionName.P1)
    assert [u.text for u in units] == ["Câu đầu.", "Câu thứ hai!", "Câu cuối?"]
    assert [u.paragraph for u in units] == [0, 0, 1]
    japanese = service.japanese_choices(state, SectionName.P1)
    assert [u.paragraph for u in japanese] == [0, 0, 1]
    for vn, ja in zip(units, japanese):
        proposal = service.suggest_japanese(state, SectionName.P1, vn.key)
        assert proposal.sentence_id == ja.sentence_id
        assert "Gợi ý theo đoạn" in proposal.message
    assert state.project.key_placements == []
    assert split_vietnamese_units("Tỉ số 2.5. Một câu nữa.")[0].text == "Tỉ số 2.5."


def test_mismatched_counts_require_manual_japanese_target(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目！三文目。", "Một câu. Hai câu.")
    units = service.vietnamese_units(state, SectionName.P1)
    assert service.suggest_japanese(state, SectionName.P1, units[1].key).sentence_id is None
    assert "Cần chọn câu Nhật tương ứng" in service.suggest_japanese(state, SectionName.P1, units[1].key).message
    assert len(service.japanese_choices(state, SectionName.P1)) == 3


def test_manual_correction_multiple_keys_and_reopen(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目。三文目。", "Một câu. Hai câu. Ba câu.")
    vietnamese = service.vietnamese_units(state, SectionName.P1)
    japanese = service.japanese_choices(state, SectionName.P1)
    first = service.add_key_draft(state, key_file(tmp_path, "key1.png"))
    second = service.add_key_draft(state, key_file(tmp_path, "key2.png"))
    assert service.suggest_japanese(state, SectionName.P1, vietnamese[1].key).sentence_id == japanese[1].sentence_id
    one = service.confirm_key_draft(state, first["id"], section=SectionName.P1,
        vietnamese_key=vietnamese[1].key, sentence_id=japanese[2].sentence_id, offset_ms=60)
    two = service.confirm_key_draft(state, second["id"], section=SectionName.P1,
        vietnamese_key=vietnamese[0].key, sentence_id=japanese[0].sentence_id)
    assert one.sentence_id == japanese[2].sentence_id and one.confirmed_vietnamese_text == "Hai câu."
    assert two.sentence_id == japanese[0].sentence_id
    reopened = service.open_project(state.directory)
    assert len(reopened.project.key_placements) == 2
    assert all(item.status != "manual_review" for item in reopened.project.key_placements)
    assert reopened.project.sentence_units[2].mapping_confirmed


def test_vietnamese_edit_keeps_japanese_key_placements(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目。", "Một câu. Hai câu.")
    vietnamese = service.vietnamese_units(state, SectionName.P1)
    japanese = service.japanese_choices(state, SectionName.P1)
    ids = []
    for number in range(2):
        draft = service.add_key_draft(state, key_file(tmp_path, f"key{number}.png"))
        placed = service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
            vietnamese_key=vietnamese[number].key, sentence_id=japanese[number].sentence_id)
        ids.append(placed.id)
    service.set_section(state, SectionName.P1, japanese="一文目。二文目。", vietnamese="Một câu. Hai câu đã sửa.")
    service.save_project(state)
    reopened = service.open_project(state.directory)
    by_id = {item.id: item for item in reopened.project.key_placements}
    assert by_id[ids[0]].status != "manual_review"
    assert by_id[ids[1]].status == "ready"
    assert reopened.project.sentence_units[0].mapping_confirmed
    assert not reopened.project.sentence_units[1].mapping_confirmed


def test_key_without_vietnamese_can_save_reopen_and_retarget(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目。", "")
    japanese = service.japanese_choices(state, SectionName.P1)
    draft = service.add_key_draft(state, key_file(tmp_path, "key.png"))
    placement = service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
        sentence_id=japanese[1].sentence_id, offset_ms=40, duration_seconds=2.5)
    assert placement.sentence_id == japanese[1].sentence_id
    assert placement.confirmed_vietnamese_text == ""
    assert placement.duration_ms == 2500 and placement.offset_ms == 40
    assert not any(unit.mapping_confirmed for unit in state.project.sentence_units)
    reopened = service.open_project(state.directory)
    assert reopened.project.key_placements[0].status == "ready"
    KeyPlacementService.retarget_key(reopened.project, reopened.directory, placement.id,
                                     japanese[0].sentence_id, force=True)
    assert service.open_project(state.directory).project.key_placements[0].sentence_id == japanese[0].sentence_id


def test_japanese_change_requires_review_without_vietnamese(tmp_path):
    service, state = setup(tmp_path, "一文目。二文目。", "")
    draft = service.add_key_draft(state, key_file(tmp_path, "key.png"))
    target = service.japanese_choices(state, SectionName.P1)[1]
    placed = service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
                                       sentence_id=target.sentence_id)
    with pytest.raises(ValueError, match="Câu Nhật đã thay đổi"):
        service.confirm_key_draft(state, service.add_key_draft(state, key_file(tmp_path, "other.png"))["id"],
                                  section=SectionName.P1, sentence_id="sen_" + "0" * 32)
    service.set_section(state, SectionName.P1, japanese="一文目。二文目を修正。", vietnamese="")
    service.save_project(state)
    reopened = service.open_project(state.directory)
    assert reopened.project.key_placements[0].id == placed.id
    assert reopened.project.key_placements[0].status == "manual_review"


def test_key_dialogs_do_not_require_vietnamese_or_pair_confirmation():
    open_source = inspect.getsource(StudioApp._open_key_entry)
    draft_source = inspect.getsource(StudioApp._render_key_draft)
    retarget_source = inspect.getsource(StudioApp._retarget_key)
    for source in (open_source, draft_source, retarget_source):
        assert "selected_vi" not in source
        assert "Tiếng Việt" not in source
        assert "context =" not in source
        assert "900x410" not in source
    for source in (draft_source, retarget_source):
        assert 'text="Lưu vị trí KEY"' in source


def test_long_scripts_cannot_expand_key_dialog_labels(tmp_path):
    vietnamese = "Một đoạn tham khảo rất dài. " * 1000
    japanese = "長い日本語の文" * 500 + "。"
    service, state = setup(tmp_path, japanese, vietnamese)
    draft = service.add_key_draft(state, key_file(tmp_path, "key.png"))
    choice = service.japanese_choices(state, SectionName.P1)[0]
    label = _key_sentence_label(choice.order + 1, choice.text)
    assert len(label) < 55 and label.endswith("…")
    assert vietnamese[:50] not in label and japanese not in label
    placed = service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
                                       sentence_id=choice.sentence_id)
    assert placed.sentence_id == choice.sentence_id
    assert service.open_project(state.directory).project.key_placements[0].status == "ready"


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
@pytest.mark.parametrize("scaling", [1.25, 1.5])
def test_key_dialog_buttons_visible_with_long_scripts_and_scaling(tmp_path, monkeypatch, scaling):
    import tkinter as tk
    from tkinter import simpledialog, ttk

    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "KEY GUI")
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", scaling)
        service = StudioService(tmp_path / "settings.json")
        service.set_directories(tmp_path / "projects", tmp_path / "output")
        app = StudioApp(root, service)
        state = app.state
        service.set_section(state, SectionName.P1,
            japanese="長い日本語の文" * 200 + "。", vietnamese="Câu Việt rất dài. " * 500)
        service.save_project(state)
        app._load_state(state)
        draft = service.add_key_draft(state, key_file(tmp_path, "key.png"))

        def visible_button(dialog, label):
            root.update()
            pending = [dialog]
            while pending:
                widget = pending.pop()
                if isinstance(widget, ttk.Button) and widget.cget("text") == label:
                    assert widget.winfo_viewable()
                    assert widget.winfo_rooty() + widget.winfo_height() <= \
                           dialog.winfo_rooty() + dialog.winfo_height()
                    return widget
                pending.extend(widget.winfo_children())
            pytest.fail(f"Missing visible button: {label}")

        app._open_key_entry("draft", draft["id"])
        dialog = root.winfo_children()[-1]
        visible_button(dialog, "Đóng")
        save = visible_button(dialog, "Lưu vị trí KEY")
        combo = next(widget for widget in dialog.winfo_children()[0].winfo_children()
                     if isinstance(widget, ttk.Combobox) and int(widget.cget("width")) == 45)
        combo.set(combo.cget("values")[0])
        save.invoke()
        placement = state.project.key_placements[0]

        app._open_key_entry("placed", placement.id)
        dialog = root.winfo_children()[-1]
        visible_button(dialog, "Lưu thời điểm")
        visible_button(dialog, "Đóng")
        visible_button(dialog, "Chọn lại câu Nhật").invoke()
        retarget_dialog = root.winfo_children()[-1]
        visible_button(retarget_dialog, "Lưu vị trí KEY")
        visible_button(retarget_dialog, "Đóng")
    finally:
        root.destroy()
