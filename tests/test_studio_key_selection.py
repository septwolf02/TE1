"""Vietnamese-first KEY selection is advisory until an editor confirms it."""
from te.project import SectionName
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


def test_vietnamese_edit_invalidates_only_affected_mapping(tmp_path):
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
    assert by_id[ids[1]].status == "manual_review"
    assert reopened.project.sentence_units[0].mapping_confirmed
    assert not reopened.project.sentence_units[1].mapping_confirmed
