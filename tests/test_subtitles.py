import json
from pathlib import Path

import jsonschema
import pytest

from te.project import ProjectManager, SectionName
from te.subtitles import SubtitleService, split_japanese_sentences, wrap_caption
from te.tts import TTSService


def setup_project(tmp_path, count=1):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "Captions")
    for number in range(1, count + 1):
        section = list(SectionName)[min(3, (number - 1) * 4 // count)]
        manager.add_segment(project, section, number, "第一文。第二文！第三文？")
    manager.save(folder, project)
    TTSService().generate_all(project, folder)
    return manager, folder, project


def test_japanese_split_quotes_and_wrap():
    text = "彼は「勝つ。」と言った。次は「行く！本当？」だった。"
    parts = split_japanese_sentences(text)
    assert "".join(parts) == text
    assert "勝つ。" in parts[0]
    assert parts[0].endswith("。」")
    assert len(parts) >= 3
    assert wrap_caption("日本語テキスト", 4) == ["日本語テ", "キスト"]
    with pytest.raises(ValueError):
        wrap_caption("abcdefghijk", 4, 2)


def test_43_segments_make_129_captions_and_persist(tmp_path):
    manager, folder, project = setup_project(tmp_path, 43)
    service = SubtitleService()
    captions = service.generate_all_captions(project)
    assert len(captions) == 129
    assert len({caption.id for caption in captions}) == 129
    assert all(len([c for c in captions if c.segment_id == s.id]) == 3 for s in project.segments)
    results = service.align_all(project, folder, provider_id="deterministic_test")
    assert set(results.values()) == {"aligned"}
    manager.save(folder, project)
    reopened = manager.open(folder)
    assert len(reopened.subtitle_captions) == 129
    assert all(not service.detect_stale_alignment(reopened, s.id) for s in reopened.segments)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/te/schemas/project-v5.json").read_text())
    jsonschema.validate(json.loads((folder / "project.json").read_text()), schema)


def test_proportional_fallback_and_local_duration(tmp_path):
    _, folder, project = setup_project(tmp_path)
    service = SubtitleService()
    assert service.align_segment(project, folder, project.segments[0].id) == "approximate"
    captions = service._captions(project, project.segments[0].id)
    assert [c.start_ms for c in captions] == [0, 400, 800]
    assert captions[-1].end_ms == 1200
    assert all(c.confidence < 1 for c in captions)
    service.validate_captions(project, project.segments[0].id, require_contiguous=True)


def test_stale_tts_rejected_and_regeneration_detected(tmp_path):
    _, folder, project = setup_project(tmp_path)
    service = SubtitleService()
    segment = project.segments[0]
    service.align_segment(project, folder, segment.id)
    TTSService().regenerate_segment(project, folder, segment.id)
    assert service.detect_stale_alignment(project, segment.id)
    segment.japanese_text += "追記。"
    assert service.align_segment(project, folder, segment.id) == "audio_stale"
    assert service.detect_stale_alignment(project, segment.id)
    TTSService().regenerate_segment(project, folder, segment.id)
    assert service.align_segment(project, folder, segment.id) == "stale_split"
    rebuilt = service.regenerate_captions(project, segment.id)
    assert len(rebuilt) == 4
    assert service.align_segment(project, folder, segment.id) == "approximate"


def test_manual_lock_split_merge_and_timing_validation(tmp_path):
    _, folder, project = setup_project(tmp_path)
    service = SubtitleService()
    segment = project.segments[0]
    service.align_segment(project, folder, segment.id)
    captions = service._captions(project, segment.id)
    first = captions[0]
    service.lock_timing(project, first.id)
    assert service.align_segment(project, folder, segment.id) == "locked"
    assert first.start_ms == 0
    service.adjust_timing(project, first.id, 0, 350)
    assert first.alignment_status == "manual" and first.end_ms == 350
    with pytest.raises(ValueError, match="overlap"):
        service.adjust_timing(project, first.id, 0, 450)
    assert first.end_ms == 350
    service.lock_timing(project, first.id, False)
    left, right = service.split_caption(project, first.id, 2)
    assert left.id == first.id
    assert left.text + right.text == "第一文。"
    merged = service.merge_captions(project, left.id, right.id)
    assert merged.id == first.id and merged.text == "第一文。"
    assert service.detect_stale_alignment(project, segment.id)
    assert service.align_segment(project, folder, segment.id, overwrite_locked=True) == "approximate"


@pytest.mark.parametrize("old_version", [1, 2, 3, 4])
def test_v1_to_v4_project_migration(tmp_path, old_version):
    manager, folder, project = setup_project(tmp_path)
    path = folder / "project.json"
    data = json.loads(path.read_text())
    data["schema_version"] = old_version
    data.pop("subtitle_captions")
    data.pop("subtitle_style")
    if old_version < 4:
        data.pop("tts_settings")
        data.pop("audio_records")
        for segment in data["segments"]:
            segment["audio_ref"] = None
    if old_version < 3:
        for key in ("media_library_path", "media_assignments", "section_intros", "media_reuse_distance"):
            data.pop(key)
    if old_version == 1:
        for segment in data["segments"]:
            for key in ("notes", "status", "audio_ref", "caption_refs", "media_placement_refs"):
                segment.pop(key)
    path.write_text(json.dumps(data, ensure_ascii=False))
    opened = manager.open(folder)
    assert opened.schema_version == 5 and opened.subtitle_captions == []
    manager.save(folder, opened)
    assert json.loads(path.read_text())["schema_version"] == 5
