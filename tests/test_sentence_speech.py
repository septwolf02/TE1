import json
import os
from pathlib import Path
import wave

import jsonschema
import pytest

from te.project import ProjectManager, SectionName
from te.timeline import TimelineService
from te.tts.sentences import SentenceSpeechService
from te.subtitles import split_japanese_sentences


def make_project(tmp_path):
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Sentence speech")
    first = manager.add_segment(project, SectionName.P1, 1, "「大谷が打った！」会場が沸いた。", "Ohtani đánh bóng. Khán đài bùng nổ.")
    second = manager.add_segment(project, SectionName.P2, 2, "次の試合へ進みます。")
    manager.save(directory, project)
    return manager, directory, project, first, second


def test_split_exact_text_stable_ids_mapping_and_sync(tmp_path):
    manager, directory, project, first, second = make_project(tmp_path)
    assert split_japanese_sentences(first.japanese_text) == ["「大谷が打った！」", "会場が沸いた。"]
    service = SentenceSpeechService()
    result = service.sync_all(project, directory)
    units = [item[0] for item in result[first.id]]
    assert "".join(unit.synthesis_text for unit in units) == first.japanese_text
    assert all(unit.segment_id == first.id and not unit.mapping_confirmed for unit in units)
    assert first.vietnamese_reference == "Ohtani đánh bóng. Khán đài bùng nổ."
    service.confirm_vietnamese_mapping(project, units[0].id, "Ohtani đánh bóng.")
    assert units[0].mapping_confirmed and units[1].vietnamese_reference is None
    manager.save(directory, project)
    assert manager.open(directory).sentence_units[0].vietnamese_reference == "Ohtani đánh bóng."
    first_ids = [unit.id for unit in units]
    assert [unit.id for unit in service.split_segment(project, first.id)] == first_ids
    captions = [c for c in project.subtitle_captions if c.segment_id == first.id]
    assert [c.sentence_id for c in captions] == first_ids
    assert [(c.start_ms, c.end_ms) for c in captions] == [(start, end) for _, start, end in result[first.id]]
    assert captions[1].start_ms - captions[0].end_ms == project.sentence_pause_ms
    timeline = TimelineService.load_timeline(project, directory)
    for unit in units:
        caption = next(c for c in timeline.captions if c.id == next(x.id for x in captions if x.sentence_id == unit.id))
        assert TimelineService.get_sentence_range(project, timeline, unit.id) == (caption.start_ms, caption.end_ms)
    assert TimelineService.get_sentence_range(project, timeline, result[second.id][0][0].id)[0] >= timeline.blocks[1].start_ms
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/te/schemas/project-v8.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((directory / "project.json").read_text(encoding="utf-8")), schema)


def test_partial_regeneration_exact_duration_caption_edit_and_pause(tmp_path):
    manager, directory, project, first, _ = make_project(tmp_path)
    service = SentenceSpeechService()
    service.sync_all(project, directory)
    records = {r.sentence_id: (r.generation_version, Path(r.path).stat().st_mtime_ns) for r in project.sentence_audio_records}
    first_units = [unit for unit in project.sentence_units if unit.segment_id == first.id]
    caption = next(c for c in project.subtitle_captions if c.sentence_id == first_units[0].id)
    caption.text = "編集した字幕"
    caption.locked = True
    manager.save(directory, project)
    first.japanese_text = "「大谷が打った！」球場が沸いた。"
    service.sync_segment(project, directory, first.id)
    current = {r.sentence_id: r for r in project.sentence_audio_records}
    assert current[first_units[0].id].generation_version == records[first_units[0].id][0]
    assert Path(current[first_units[0].id].path).stat().st_mtime_ns == records[first_units[0].id][1]
    assert current[first_units[1].id].generation_version == records[first_units[1].id][0] + 1
    assert next(c for c in project.subtitle_captions if c.sentence_id == first_units[0].id).text == "編集した字幕"
    assert next(c for c in project.subtitle_captions if c.sentence_id == first_units[0].id).locked
    with wave.open(next(r.path for r in project.audio_records if r.segment_id == first.id), "rb") as audio:
        measured = audio.getnframes() / audio.getframerate()
    assert measured == next(r.duration_seconds for r in project.audio_records if r.segment_id == first.id)
    project.sentence_pause_ms = 200
    version = next(r.generation_version for r in project.audio_records if r.segment_id == first.id)
    service.sync_segment(project, directory, first.id)
    assert next(r.generation_version for r in project.audio_records if r.segment_id == first.id) == version + 1
    assert current[first_units[0].id].generation_version == records[first_units[0].id][0]
    assert current[first_units[1].id].generation_version == records[first_units[1].id][0] + 1
    assert next(r for r in project.sentence_audio_records if r.sentence_id == first_units[0].id).generation_version == 1


def test_v6_project_migrates_without_deleting_legacy_audio(tmp_path):
    manager, directory, project, first, _ = make_project(tmp_path)
    from te.tts import TTSService
    legacy = TTSService().generate_segment(project, directory, first.id)
    old_file = Path(legacy.path)
    data = project.to_dict()
    data["schema_version"] = 6
    for key in ("sentence_units", "sentence_audio_records", "sentence_pause_ms"):
        data.pop(key)
    for caption in data["subtitle_captions"]:
        caption.pop("sentence_id", None)
    (directory / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reopened = manager.open(directory)
    assert reopened.schema_version == 8 and reopened.sentence_units == []
    assert old_file.is_file() and reopened.audio_records[0].path == str(old_file)
    SentenceSpeechService().sync_segment(reopened, directory, first.id, rebuild_timeline=False)
    assert old_file.is_file()


def test_insert_delete_and_settings_staleness(tmp_path):
    manager, directory, project, first, _ = make_project(tmp_path)
    service = SentenceSpeechService()
    service.sync_all(project, directory)
    first_id = next(unit.id for unit in project.sentence_units if unit.segment_id == first.id)
    project.tts_settings.speaking_rate = 1.15
    assert service.detect_stale_sentence_audio(project, first_id)
    service.sync_segment(project, directory, first.id)
    assert service.get_sentence_audio_status(project, first_id) == "ready"
    first.japanese_text = "「大谷が打った！」新しい一文です。会場が沸いた。"
    service.sync_segment(project, directory, first.id)
    units = [u for u in project.sentence_units if u.segment_id == first.id]
    assert len(units) == 3 and units[0].id == first_id
    removed = units[1].id
    first.japanese_text = "「大谷が打った！」会場が沸いた。"
    service.sync_segment(project, directory, first.id)
    assert removed not in {u.id for u in project.sentence_units}
    assert Path(directory / "generated" / "audio" / "sentences" / f"{removed}.wav").is_file()
    assert len([c for c in project.subtitle_captions if c.segment_id == first.id]) == 2
    assert manager.open(directory).sentence_pause_ms == 120


def test_timeline_rebuild_keeps_locked_visual_and_effect(tmp_path):
    from te.media import MediaAssignmentService
    from te.project.models import MediaKind
    manager, directory, project, first, _ = make_project(tmp_path)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"placeholder")
    asset = manager.add_media(project, MediaKind.IMAGE, source)
    MediaAssignmentService().assign_media(project, first.id, asset.id, locked=True)
    manager.save(directory, project)
    service = SentenceSpeechService()
    service.sync_all(project, directory)
    timeline = TimelineService.load_timeline(project, directory)
    block = TimelineService.get_segment_block(timeline, first.id)
    block.effect_locked = True
    block.effect = "pan_left"
    TimelineService.save_timeline(project, directory, timeline)
    first.japanese_text = first.japanese_text.replace("会場", "球場")
    service.sync_segment(project, directory, first.id)
    rebuilt = TimelineService.load_timeline(project, directory)
    block = TimelineService.get_segment_block(rebuilt, first.id)
    assert block.visual_locked and block.effect_locked and block.effect == "pan_left"
    assert project.media_assignments[0].locked


def test_sentence_failure_preserves_other_audio(tmp_path):
    from te.tts import FakeTTSProvider, TTSService

    class Flaky(FakeTTSProvider):
        provider_id = "flaky"

        def synthesize(self, text, settings, output_path):
            if "会場" in text:
                raise RuntimeError("local provider failed")
            return super().synthesize(text, settings, output_path)

    manager, directory, project, first, _ = make_project(tmp_path)
    project.tts_settings.provider = "flaky"
    manager.save(directory, project)
    service = SentenceSpeechService(TTSService({"flaky": Flaky()}))
    with pytest.raises(RuntimeError, match="local provider failed"):
        service.sync_segment(project, directory, first.id)
    records = [r for r in project.sentence_audio_records]
    assert [r.status for r in records] == ["ready", "failed"]
    assert Path(records[0].path).is_file()
    assert manager.open(directory).sentence_audio_records[0].status == "ready"

    class Working(FakeTTSProvider):
        provider_id = "flaky"

    recovered = SentenceSpeechService(TTSService({"flaky": Working()}))
    recovered.sync_segment(project, directory, first.id, rebuild_timeline=False)
    assert [r.generation_version for r in project.sentence_audio_records] == [1, 2]


@pytest.mark.skipif(os.environ.get("TE_VOICEVOX_INTEGRATION") != "1", reason="opt-in local engine test")
def test_real_voicevox_sentence_timing(tmp_path):
    from te.project.models import TTSSettings
    manager, directory, project, first, _ = make_project(tmp_path)
    project.tts_settings = TTSSettings(provider="voicevox", sample_rate=24000)
    manager.save(directory, project)
    anchors = SentenceSpeechService().sync_segment(project, directory, first.id, rebuild_timeline=False)
    assert len(anchors) == 2
    assert anchors[1][2] > anchors[1][1] > anchors[0][2]
    assert all(Path(r.path).is_file() and r.credit == "青山龍星"
               for r in project.sentence_audio_records)
