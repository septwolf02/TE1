"""Japanese sentence IDs alone control KEY placement; Vietnamese is optional context."""
import inspect

import pytest

from te.project import SectionName
from te.media import KeyPlacementService
from te.studio.gui import StudioApp
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
    draft_source = inspect.getsource(StudioApp._render_key_draft)
    retarget_source = inspect.getsource(StudioApp._retarget_key)
    for source in (draft_source, retarget_source):
        assert "selected_vi" not in source
        assert "messagebox.askyesno" not in source
        assert 'text="Lưu vị trí KEY"' in source
