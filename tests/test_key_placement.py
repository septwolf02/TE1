"""KEY reveal contract, including a real FFmpeg frame check with FakeTTS audio."""
import json
import math
import os
from pathlib import Path
import subprocess

import jsonschema
import pytest

from te.media import KeyPlacementService, MediaAssignmentService
from te.project import MediaKind, ProjectManager, SectionName
from te.timeline import TimelineService
from te.tts import SentenceSpeechService
from te.tts.key_demo import run_demo
from te.render import RenderService, RenderSettings


def project_with_key_media(tmp_path):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "KEY")
    manager.add_segment(project, SectionName.P1, 1, "始まりました。", "Bắt đầu.")
    reveal_segment = manager.add_segment(project, SectionName.P2, 2,
        "まず、投手が構えた。大谷が打った！", "Đầu tiên, pitcher chuẩn bị. Ohtani đánh bóng!")
    images = []
    for name, kind in (("a.jpg", MediaKind.IMAGE), ("b.jpg", MediaKind.IMAGE),
                       ("key.jpg", MediaKind.KEY_IMAGE)):
        path = tmp_path / name
        path.write_bytes(name.encode())
        images.append(manager.add_media(project, kind, path))
    intro_path = tmp_path / "P1.mp4"
    intro_path.write_bytes(b"intro")
    intro = manager.add_media(project, MediaKind.SECTION_INTRO_VIDEO, intro_path, SectionName.P1)
    intro.duration_seconds = 0.5
    project.section_intros["P1"] = intro.id
    project.random_seed = 31
    manager.save(folder, project)
    SentenceSpeechService().sync_all(project, folder)
    target = next(unit for unit in project.sentence_units
                  if unit.segment_id == reveal_segment.id and unit.order == 2)
    return manager, folder, project, target, images


def test_confirmation_timing_offset_lock_random_exclusion_and_rebuild(tmp_path):
    manager, folder, project, target, images = project_with_key_media(tmp_path)
    service = KeyPlacementService()
    context = service.context(project, target.id)
    assert context.japanese_text == "大谷が打った！"
    assert context.vietnamese_segment_text == "Đầu tiên, pitcher chuẩn bị. Ohtani đánh bóng!"
    assert not context.mapping_confirmed and context.vietnamese_sentence_text is None
    with pytest.raises(ValueError, match="Confirm"):
        service.assign_key(project, folder, images[2].id, target.id)
    service.confirm_mapping(project, folder, target.id, "Ohtani đánh bóng!")
    placement = service.assign_key(project, folder, images[2].id, target.id, offset_ms=-40)
    created = service.assign_random_images(project, folder)
    assert len(created) == 2
    assert all(a.media_id != images[2].id and a.assignment_type == "random"
               for a in project.media_assignments)
    assert service.assign_random_images(project, folder) == ()
    timeline_service = TimelineService()
    timeline, _ = timeline_service.rebuild_timeline(project, folder)
    spoken_start, spoken_end = timeline_service.get_sentence_range(project, timeline, target.id)
    start, end = service.resolve(project, timeline, placement)
    assert start == spoken_start - 40
    assert end == min(start + spoken_end - spoken_start,
                      timeline_service.get_segment_block(timeline, target.segment_id).end_ms)
    assert timeline.intros[0].end_ms == 500 and spoken_start > 500
    key_visual = next(item for item in timeline.placements if item.id == placement.id)
    assert (key_visual.start_ms, key_visual.end_ms) == (start, end)
    assert all(item.media_id != images[2].id or item.start_ms >= start
               for item in timeline.placements)
    assert placement.locked
    service.set_timing(project, folder, placement.id, offset_ms=50, duration_ms=300)
    timeline, _ = timeline_service.rebuild_timeline(project, folder)
    assert service.resolve(project, timeline, placement) == (spoken_start + 50, spoken_start + 350)
    assert manager.open(folder).key_placements[0].offset_ms == 50
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/te/schemas/project-v8.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((folder / "project.json").read_text(encoding="utf-8")), schema)


def test_audio_change_recalculates_and_removed_sentence_needs_review(tmp_path):
    _, folder, project, target, images = project_with_key_media(tmp_path)
    service = KeyPlacementService()
    service.confirm_mapping(project, folder, target.id, "Ohtani đánh bóng!")
    placement = service.assign_key(project, folder, images[2].id, target.id)
    timeline, _ = TimelineService().rebuild_timeline(project, folder)
    old_start = service.resolve(project, timeline, placement)[0]
    project.tts_settings.speaking_rate = 2.0
    SentenceSpeechService().sync_all(project, folder)
    new_timeline = TimelineService.load_timeline(project, folder)
    assert service.resolve(project, new_timeline, placement)[0] < old_start
    segment = next(s for s in project.segments if s.id == target.segment_id)
    segment.japanese_text = "まず、投手が構えた。"
    SentenceSpeechService().sync_segment(project, folder, segment.id)
    assert placement.status == "manual_review"
    assert not any(item.media_id == placement.media_id
                   for item in TimelineService.load_timeline(project, folder).placements)
    assert (folder / "generated" / "audio" / "sentences" / f"{target.id}.wav").is_file()
    with pytest.raises(ValueError, match="Unlock"):
        service.retarget_key(project, folder, placement.id,
            next(unit.id for unit in project.sentence_units if unit.segment_id == segment.id))
    new_target = next(unit for unit in project.sentence_units if unit.segment_id == segment.id)
    service.confirm_mapping(project, folder, new_target.id, "Pitcher chuẩn bị.")
    service.retarget_key(project, folder, placement.id, new_target.id, force=True)
    timeline, _ = TimelineService().rebuild_timeline(project, folder)
    assert placement.status == "ready"
    assert any(item.id == placement.id for item in timeline.placements)


def test_multiple_key_images_and_small_library_random_reuse(tmp_path):
    manager, folder, project, target, images = project_with_key_media(tmp_path)
    service = KeyPlacementService()
    first_sentence = next(unit for unit in project.sentence_units
                          if unit.segment_id == target.segment_id and unit.order == 1)
    second_key_path = tmp_path / "key2.jpg"
    second_key_path.write_bytes(b"second KEY")
    second_key = manager.add_media(project, MediaKind.KEY_IMAGE, second_key_path)
    manager.add_segment(project, SectionName.P2, 3, "続きます。")
    manager.add_segment(project, SectionName.P2, 4, "最後です。")
    manager.save(folder, project)
    service.confirm_mapping(project, folder, first_sentence.id, "Pitcher chuẩn bị.")
    service.confirm_mapping(project, folder, target.id, "Ohtani đánh bóng!")
    service.assign_key(project, folder, second_key.id, first_sentence.id)
    service.assign_key(project, folder, images[2].id, target.id)
    created = service.assign_random_images(project, folder)
    assert len(created) == 4
    assigned = [a.media_id for a in project.media_assignments]
    assert all(left != right for left, right in zip(assigned, assigned[1:]))
    assert set(assigned) == {images[0].id, images[1].id}
    assert not set(assigned) & {images[2].id, second_key.id}
    assert service.assign_random_images(project, folder) == ()
    for segment in project.segments:
        MediaAssignmentService().unassign_media(project, segment.id)
    manager.save(folder, project)
    service.assign_random_images(project, folder)
    assert [a.media_id for a in project.media_assignments] == assigned


def test_v7_migration_and_missing_target_is_flagged(tmp_path):
    manager, folder, project, target, images = project_with_key_media(tmp_path)
    service = KeyPlacementService()
    service.confirm_mapping(project, folder, target.id, "Ohtani đánh bóng!")
    service.assign_key(project, folder, images[2].id, target.id)
    data = project.to_dict()
    data["schema_version"] = 7
    data.pop("key_placements"); data.pop("random_seed")
    (folder / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    migrated = manager.open(folder)
    assert migrated.schema_version == 8 and migrated.key_placements == [] and migrated.random_seed == 0
    data = project.to_dict()
    data["sentence_units"] = [unit for unit in data["sentence_units"] if unit["id"] != target.id]
    data["sentence_audio_records"] = [record for record in data["sentence_audio_records"] if record["sentence_id"] != target.id]
    data["subtitle_captions"] = [caption for caption in data["subtitle_captions"] if caption["sentence_id"] != target.id]
    for index, caption in enumerate((c for c in data["subtitle_captions"] if c["segment_id"] == target.segment_id), 1):
        caption["order"] = index
    for segment in data["segments"]:
        segment["caption_refs"] = [c["id"] for c in data["subtitle_captions"] if c["segment_id"] == segment["id"]]
    (folder / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reopened = manager.open(folder)
    assert reopened.key_placements[0].status == "manual_review"


def _pixel(path, seconds):
    result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path),
        "-frames:v", "1", "-vf", "crop=2:2:480:200,scale=1:1", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return tuple(result.stdout[:3])


def test_fake_audio_real_ffmpeg_key_reveal_frames(tmp_path):
    output, start, end = run_demo(tmp_path, provider="fake", vn_text="Ohtani đánh cú home run lội ngược dòng!",
                                  font_path=os.environ.get("TE_JAPANESE_FONT_PATH"))
    assert output.is_file()
    fps = 30
    key_frame = math.ceil(start * fps / 1000)
    before = _pixel(output, (key_frame - 1) / fps)
    after = _pixel(output, key_frame / fps)
    assert before[0] < 100 and after[0] > 180 and after[2] > 100
    assert end > start
    project_dir = output.parents[2]
    project = ProjectManager().open(project_dir)
    timeline = TimelineService.load_timeline(project, project_dir)
    TimelineService.set_transition(timeline, SectionName.P2, "crossfade")
    TimelineService.save_timeline(project, project_dir, timeline)
    RenderService().render_preview(project, timeline, project_dir, overwrite=True,
        settings=RenderSettings(960, 540, 30, 28, "veryfast",
                                font_path=os.environ.get("TE_JAPANESE_FONT_PATH")))
    assert _pixel(output, (key_frame - 1) / fps)[0] < 100
    assert _pixel(output, key_frame / fps)[0] > 180


@pytest.mark.skipif(os.environ.get("TE_VOICEVOX_INTEGRATION") != "1", reason="opt-in local VOICEVOX")
def test_real_voicevox_key_smoke(tmp_path):
    output, start, end = run_demo(tmp_path, provider="voicevox",
        vn_text="Ohtani đánh cú home run lội ngược dòng!",
        font_path=os.environ.get("TE_JAPANESE_FONT_PATH"))
    assert output.is_file() and end > start
