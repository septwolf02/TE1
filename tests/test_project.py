import json
from pathlib import Path

import jsonschema
import pytest

from te.project import ProjectManager, SectionName
from te.project.models import MediaKind
from te.timeline import Caption, Placement, Timeline
from te.project.models import new_id


def test_create_edit_save_open_and_schema(tmp_path):
    manager = ProjectManager()
    folder = tmp_path / "story"
    project = manager.create(folder, "野球の物語")
    segment = manager.add_segment(project, SectionName.P1, 1, "最初の文。", "Câu đầu.")
    manager.edit_segment(project, segment.id, japanese_text="改訂した文。")
    manager.save(folder, project)
    opened = manager.open(folder)
    assert opened.segments[0].id == segment.id
    assert opened.segments[0].japanese_text == "改訂した文。"
    assert [s.id for s in opened.sections] == list(SectionName)
    schemas = Path(__file__).resolve().parents[1] / "src" / "te" / "schemas"
    for name, schema_name in [("project.json", "project-v5.json"), ("timeline.json", "timeline-v2.json")]:
        data = json.loads((folder / name).read_text(encoding="utf-8"))
        schema = json.loads((schemas / schema_name).read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)


def test_external_media_and_timeline_references(tmp_path):
    source = tmp_path / "original.jpg"
    source.write_bytes(b"original media")
    folder = tmp_path / "story"
    manager = ProjectManager()
    project = manager.create(folder, "Story")
    segment = manager.add_segment(project, SectionName.P2, 1, "大事な場面。")
    asset = manager.add_media(project, MediaKind.KEY_IMAGE, source)
    manager.save(folder, project)
    assert source.read_bytes() == b"original media"
    assert manager.open(folder).media[0].path == str(source)
    timeline = Timeline(project.id, captions=[Caption(new_id("cap"), segment.id, "大事な場面。", 0, 1200)],
        placements=[Placement(new_id("plc"), asset.id, SectionName.P2, 0, 1200, segment.id)])
    timeline.validate(project)
    assert timeline.to_dict()["placements"][0]["media_id"] == asset.id


def test_invalid_data_does_not_replace_saved_project(tmp_path):
    manager = ProjectManager()
    folder = tmp_path / "story"
    project = manager.create(folder, "Story")
    manager.add_segment(project, SectionName.P1, 1, "有効。")
    with pytest.raises(ValueError):
        manager.add_segment(project, SectionName.P2, 1, "重複。")
    manager.save(folder, project)
    old = (folder / "project.json").read_bytes()
    project.segments[0].japanese_text = ""
    with pytest.raises(ValueError):
        manager.save(folder, project)
    assert (folder / "project.json").read_bytes() == old


def test_reject_unknown_version_and_broken_reference(tmp_path):
    manager = ProjectManager()
    folder = tmp_path / "story"
    project = manager.create(folder, "Story")
    data = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    data["schema_version"] = 6
    (folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        manager.open(folder)
    data["schema_version"] = 5
    (folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    timeline = Timeline(project.id, captions=[Caption(new_id("cap"), new_id("seg"), "文", 0, 100)])
    (folder / "timeline.json").write_text(json.dumps(timeline.to_dict()), encoding="utf-8")
    with pytest.raises(ValueError, match="segment"):
        manager.open(folder)


def test_intro_video_is_bound_to_its_section(tmp_path):
    source = tmp_path / "intro.mp4"
    source.write_bytes(b"source")
    manager = ProjectManager()
    project = manager.create(tmp_path / "story", "Story")
    intro = manager.add_media(project, MediaKind.SECTION_INTRO_VIDEO, source, SectionName.P1)
    wrong = Timeline(project.id, placements=[Placement(new_id("plc"), intro.id, SectionName.P2, 0, 100)])
    with pytest.raises(ValueError, match="Intro video"):
        wrong.validate(project)
    assert source.read_bytes() == b"source"
