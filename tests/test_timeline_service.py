import json
from pathlib import Path

import jsonschema
import pytest

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import AssignmentType, MediaKind, ProjectManager, SectionName
from te.subtitles import SubtitleService
from te.timeline import TimelineService
from te.timeline.models import Timeline
from te.tts import TTSService


def prepared(tmp_path, count=4):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "Timeline")
    for n in range(count):
        section = list(SectionName)[min(3, n * 4 // count)]
        manager.add_segment(project, section, n + 1, "一文。二文！三文？")
    manager.save(folder, project)
    TTSService().generate_all(project, folder)
    SubtitleService().align_all(project, folder)
    return manager, folder, project


def test_43_segments_129_absolute_captions_and_reopen(tmp_path):
    manager, folder, project = prepared(tmp_path, 43)
    service = TimelineService()
    timeline, warnings = service.build_timeline(project, folder)
    assert len(timeline.blocks) == 43 and len(timeline.captions) == 129
    assert timeline.blocks[0].start_ms == 0
    assert timeline.blocks[-1].end_ms == 43000
    assert all(timeline.blocks[i].end_ms == timeline.blocks[i + 1].start_ms for i in range(42))
    assert timeline.captions[3].start_ms == 1000
    assert len(timeline.transitions) == 3
    assert service.get_section_range(timeline, SectionName.P4)[1] == 43000
    assert len(warnings) == 43
    assert all(block.visual_status == "missing" for block in timeline.blocks)
    reopened = service.load_timeline(manager.open(folder), folder)
    assert len(reopened.blocks) == 43 and not service.detect_stale_timeline(project, reopened)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/te/schemas/timeline-v2.json").read_text())
    jsonschema.validate(json.loads((folder / "timeline.json").read_text()), schema)


def test_intro_key_effect_video_policy_and_rebuild(tmp_path):
    _, folder, project = prepared(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    for name in ("image.jpg", "key.jpg", "clip.mp4", "P1.mp4", "P2.mp4", "P3.mp4", "P4.mp4"):
        path = source / name
        path.write_bytes(name.encode())
        files[name] = path
    library = MediaLibraryService()
    library.scan_library(project, source)
    by_name = {asset.filename: asset for asset in project.media}
    for section in SectionName:
        intro = by_name[f"{section}.mp4"]
        intro.duration_seconds = 2.0
        library.assign_section_intro(project, section, intro.id)
    key = library.mark_key(project, by_name["key.jpg"].id)
    assign = MediaAssignmentService()
    assign.assign_media(project, project.segments[0].id, by_name["image.jpg"].id, effect_preference="pan_left", locked=True)
    assign.assign_media(project, project.segments[1].id, by_name["clip.mp4"].id)
    assign.assign_media(project, project.segments[2].id, key.id, assignment_type=AssignmentType.KEY, locked=True)
    service = TimelineService()
    timeline, warnings = service.build_timeline(project, folder, video_policy="loop_to_segment")
    assert len(timeline.intros) == 4
    assert [intro.start_ms for intro in timeline.intros] == [0, 3000, 6000, 9000]
    assert timeline.blocks[0].start_ms == 2000
    assert timeline.blocks[1].video_policy == "loop_to_segment"
    assert timeline.blocks[0].effect == "pan_left" and timeline.blocks[0].visual_locked
    assert timeline.blocks[2].media_id == key.id
    assert timeline.blocks[2].start_ms > timeline.blocks[1].end_ms
    assert timeline.captions[0].start_ms >= timeline.blocks[0].start_ms
    service.lock_effect(timeline, project.segments[0].id)
    with pytest.raises(ValueError, match="locked"):
        service.set_effect(timeline, project.segments[0].id, "pan_right")
    service.set_transition(timeline, SectionName.P2, "fade")
    service.assign_section_music(timeline, SectionName.P1, source / "music.wav", volume=0.3, fade_in_ms=200)
    service.set_headline_config(timeline, darkening=0.7)
    rebuilt, _ = service.build_timeline(project, folder, previous=timeline)
    assert rebuilt.blocks[0].effect == "pan_left" and rebuilt.blocks[0].effect_locked
    assert rebuilt.transitions[0].preset == "fade"
    assert rebuilt.music[0].volume == 0.3 and rebuilt.headline["darkening"] == 0.7
    assert len(warnings) == 1


def test_stale_audio_and_subtitles_rejected(tmp_path):
    _, folder, project = prepared(tmp_path)
    service = TimelineService()
    timeline, _ = service.build_timeline(project, folder)
    project.segments[0].japanese_text += "追記"
    assert service.detect_stale_timeline(project, timeline)
    with pytest.raises(ValueError, match="TTS"):
        service.build_timeline(project, folder)
    TTSService().regenerate_segment(project, folder, project.segments[0].id)
    with pytest.raises(ValueError, match="subtitles"):
        service.build_timeline(project, folder)


def test_timeline_v1_migration(tmp_path):
    _, folder, project = prepared(tmp_path)
    path = folder / "timeline.json"
    path.write_text(json.dumps({"schema_version": 1, "project_id": project.id, "captions": [], "placements": []}))
    migrated = TimelineService().load_timeline(project, folder)
    assert migrated.schema_version == 2 and not migrated.blocks
    rebuilt, _ = TimelineService().rebuild_timeline(project, folder)
    assert len(rebuilt.blocks) == 4
