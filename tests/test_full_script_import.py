"""Deterministic full-script import tests without a desktop display."""
from copy import deepcopy

import pytest

from te.project.models import SectionName
from te.project import ProjectManager
from te.studio.full_script_import import parse_full_script
from te.studio.service import StudioService


def make_service(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    return service


def test_complete_unicode_multiline_and_alone_markers():
    text = "  p1  \nTiếng Việt: ‘đẹp’!\n\nĐoạn hai.\n P2\n二番目です。\nP3\n文：三。\np4\n終わり。"
    parsed = parse_full_script(text)
    assert tuple(parsed) == ("P1", "P2", "P3", "P4")
    assert parsed["P1"] == "Tiếng Việt: ‘đẹp’!\n\nĐoạn hai.\n"
    assert parsed["P4"] == "終わり。"
    assert parse_full_script("P1\nThis P2 is inline.")["P1"] == "This P2 is inline."


@pytest.mark.parametrize("labels", [("P1",), ("P1", "P2"), ("P1", "P3"), ("P2", "P4")])
def test_partial_and_skipped_sections(labels):
    parsed = parse_full_script("\n".join(f"{label}\nNội dung {label}." for label in labels))
    assert tuple(parsed) == labels


@pytest.mark.parametrize("text, expected", [
    ("P1\na\nP1\nb", "Trùng"),
    ("P3\na\nP2\nb", "thứ tự"),
    ("Lời mở đầu\nP1\na", "trước nhãn"),
    ("Không có nhãn", "trước nhãn"),
])
def test_invalid_markers(text, expected):
    with pytest.raises(ValueError, match=expected):
        parse_full_script(text)


def test_mismatched_labels_preview_and_existing_replacement(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Import")
    service.set_section(state, SectionName.P1, vietnamese="Cũ.", japanese="古い。")
    service.set_section(state, SectionName.P2, vietnamese="Xóa.", japanese="消す。")
    state.motion_preset = "pan"
    state.project.tts_settings.voice_id = "後鬼"
    service.save_project(state)
    before = deepcopy(state.sections)
    plan = service.prepare_full_script_import(state, "P1\nMới.\nP3\nBa.", "P1\n新しい。\nP4\n四。")
    assert state.sections == before
    assert plan.overwritten == ("P1",)
    assert plan.cleared == ("P2",)
    assert "P4" in plan.warnings[0] and "P3" in plan.warnings[0]
    assert service.open_project(state.directory).sections == before
    service.apply_full_script_import(state, plan)
    reopened = service.open_project(state.directory)
    assert reopened.sections == plan.sections
    assert reopened.motion_preset == "pan"
    assert reopened.project.tts_settings.voice_id == "後鬼"
    assert reopened.sections["P2"] == {"vi": "", "ja": ""}


def test_different_sentence_counts_do_not_assign_sentence_mapping(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Sentence counts")
    plan = service.prepare_full_script_import(state, "P1\nMột câu Việt.",
                                               "P1\n一文目です。二文目です。")
    assert plan.warnings == ()
    service.apply_full_script_import(state, plan)
    units = state.project.sentence_units
    assert len(units) == 2
    assert all(not unit.mapping_confirmed and unit.vietnamese_reference is None for unit in units)


def test_key_assignment_is_flagged_and_preserved_for_review(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("KEY import")
    service.set_section(state, SectionName.P1, vietnamese="Câu Việt đầu.", japanese="最初の文。")
    service.save_project(state)
    key_file = tmp_path / "key.png"
    key_file.write_bytes(b"key")
    draft = service.add_key_draft(state, key_file)
    sentence_id = state.project.sentence_units[0].id
    placement = service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
                                          sentence_id=sentence_id, vietnamese_text="Câu Việt đầu.")
    plan = service.prepare_full_script_import(state, "P1\nCâu Việt mới.", "P1\n最初の文。")
    assert plan.key_review == (placement.id,)
    assert state.project.key_placements[0].status == "ready"
    service.apply_full_script_import(state, plan)
    reopened = service.open_project(state.directory)
    assert reopened.project.key_placements[0].id == placement.id
    assert reopened.project.key_placements[0].status == "manual_review"
    assert reopened.project.key_placements[0].confirmed_vietnamese_text == "Câu Việt đầu."


def test_failed_import_does_not_mutate_project(tmp_path):
    service = make_service(tmp_path)
    state = service.create_project("Old segments")
    service.set_section(state, SectionName.P1, vietnamese="Một.", japanese="一。")
    service.save_project(state)
    before = deepcopy(state.sections)
    with pytest.raises(ValueError):
        service.prepare_full_script_import(state, "P1\nMới.", "P1\n新。\nP1\n再。")
    assert state.sections == before


def test_replaces_legacy_multi_segment_section_without_losing_settings(tmp_path):
    service = make_service(tmp_path)
    directory = tmp_path / "legacy"
    manager = ProjectManager()
    project = manager.create(directory, "Legacy")
    manager.add_segment(project, SectionName.P1, 1, "一。", "Một.")
    manager.add_segment(project, SectionName.P1, 2, "二。", "Hai.")
    manager.save(directory, project)
    state = service.open_project(directory)
    state.project.random_seed = 1357
    plan = service.prepare_full_script_import(state, "P1\nCâu mới.", "P1\n新しい。")
    assert plan.collapsed == ("P1",)
    service.apply_full_script_import(state, plan)
    reopened = service.open_project(directory)
    assert reopened.project.random_seed == 1357
    assert len(reopened.project.segments) == 1
    assert reopened.sections["P1"] == {"vi": "Câu mới.", "ja": "新しい。"}
