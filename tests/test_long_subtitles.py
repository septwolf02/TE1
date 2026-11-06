"""Long editable Japanese captions remain complete and timed to sentence WAVs."""
from pathlib import Path

import pytest

from te.media import KeyPlacementService
from te.project import MediaKind, ProjectManager, SectionName
from te.subtitles import SubtitleService, split_caption_text, wrap_caption
from te.timeline import TimelineService
from te.tts import SentenceSpeechService


def long_sentence(length: int) -> str:
    phrase = "「監督は静かに語り、あの一球から試合の空気が変わった」と振り返り、"
    return (phrase * ((length // len(phrase)) + 2))[:length - 1] + "。"


@pytest.mark.parametrize("length", [50, 100, 230])
def test_sentence_caption_split_preserves_all_characters_and_wav_timing(tmp_path, length):
    text = long_sentence(length)
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Long Japanese")
    segment = manager.add_segment(project, SectionName.P1, 1, text)
    project.subtitle_style["max_chars_per_line"] = 20
    manager.save(directory, project)
    anchors = SentenceSpeechService().sync_segment(project, directory, segment.id, rebuild_timeline=False)
    captions = [caption for caption in project.subtitle_captions if caption.segment_id == segment.id]
    assert len(captions) >= 2
    assert "".join(caption.text for caption in captions) == text
    assert all(len(caption.text) <= 40 and len(wrap_caption(caption.text, 20, 2)) <= 2
               for caption in captions)
    assert all(caption.sentence_id == anchors[0][0].id for caption in captions)
    assert captions[0].start_ms == anchors[0][1] == 0
    assert captions[-1].end_ms == anchors[0][2]
    assert all(left.end_ms == right.start_ms and left.end_ms - left.start_ms >= 100
               for left, right in zip(captions, captions[1:]))
    timeline, _ = TimelineService().build_timeline(project, directory)
    assert TimelineService.get_sentence_range(project, timeline, anchors[0][0].id) == (0, anchors[0][2])
    reopened = manager.open(directory)
    assert "".join(item.text for item in reopened.subtitle_captions) == text


def test_natural_boundaries_quotes_and_configurable_limit():
    text = "「大谷は語った、ここからが勝負だ」そして観客は沸いた、最後の一球へ向かう。" * 4
    for limit in (12, 24):
        chunks = split_caption_text(text, limit, 2)
        assert "".join(chunks) == text
        assert all(len(chunk) <= limit * 2 for chunk in chunks)
        assert any(chunk.endswith("、") or chunk.endswith("。」") or chunk.endswith("。") for chunk in chunks[:-1])


def test_legacy_subtitle_service_auto_splits_long_segment(tmp_path):
    text = long_sentence(210)
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Legacy subtitle")
    segment = manager.add_segment(project, SectionName.P1, 1, text)
    captions = SubtitleService().split_segment_into_captions(project, segment.id)
    assert len(captions) > 1 and "".join(item.text for item in captions) == text
    assert all(len(item.text) <= 48 for item in captions)


def test_reflow_preserves_key_sentence_start_without_regenerating_wav(tmp_path):
    first_text = long_sentence(180)
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Stable KEY")
    segment = manager.add_segment(project, SectionName.P1, 1, first_text + "秘密が明かされた。", "Bí mật được tiết lộ.")
    project.subtitle_style["max_chars_per_line"] = 200
    image = tmp_path / "key.png"
    image.write_bytes(b"source image")
    asset = manager.add_media(project, MediaKind.KEY_IMAGE, image)
    manager.save(directory, project)
    speech = SentenceSpeechService()
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    target = next(unit for unit in project.sentence_units if unit.order == 2)
    KeyPlacementService.confirm_mapping(project, directory, target.id, "Bí mật được tiết lộ.")
    placement = KeyPlacementService.assign_key(project, directory, asset.id, target.id, duration_ms=300)
    before, _ = TimelineService().build_timeline(project, directory)
    before_start = TimelineService.get_sentence_range(project, before, target.id)[0]
    generations = {record.sentence_id: record.generation_version for record in project.sentence_audio_records}
    project.subtitle_style["max_chars_per_line"] = 24
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    after, _ = TimelineService().build_timeline(project, directory)
    after_start = TimelineService.get_sentence_range(project, after, target.id)[0]
    assert after_start == before_start
    assert KeyPlacementService.resolve(project, after, placement)[0] == before_start
    assert {record.sentence_id: record.generation_version for record in project.sentence_audio_records} == generations
    assert len([caption for caption in project.subtitle_captions if caption.sentence_id != target.id]) > 1


def test_changed_long_sentence_updates_auto_captions_and_only_its_wav(tmp_path):
    first_text = long_sentence(125)
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Partial sentence update")
    segment = manager.add_segment(project, SectionName.P1, 1, first_text + "次の文。")
    manager.save(directory, project)
    speech = SentenceSpeechService()
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    first, second = sorted(project.sentence_units, key=lambda unit: unit.order)
    records = {item.sentence_id: item for item in project.sentence_audio_records}
    second_version = records[second.id].generation_version
    second_mtime = Path(records[second.id].path).stat().st_mtime_ns
    replacement = first_text[:-1] + "勝負。"
    segment.japanese_text = replacement + "次の文。"
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    first_captions = [item for item in project.subtitle_captions if item.sentence_id == first.id]
    assert "".join(item.text for item in first_captions) == replacement
    assert len(first_captions) > 1
    updated = {item.sentence_id: item for item in project.sentence_audio_records}
    assert updated[first.id].generation_version > records[first.id].generation_version
    assert updated[second.id].generation_version == second_version
    assert Path(updated[second.id].path).stat().st_mtime_ns == second_mtime


def test_manual_caption_text_and_split_survive_audio_sync(tmp_path):
    manager = ProjectManager()
    directory = tmp_path / "project"
    project = manager.create(directory, "Editable caption")
    segment = manager.add_segment(project, SectionName.P1, 1, "長い言葉が続いた。")
    manager.save(directory, project)
    speech = SentenceSpeechService()
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    original = project.subtitle_captions[0]
    first, second = SubtitleService().split_caption(project, original.id, 4)
    first.text = "編集した字"
    speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    captions = [item for item in project.subtitle_captions if item.segment_id == segment.id]
    assert [item.id for item in captions] == [first.id, second.id]
    assert [item.text for item in captions] == ["編集した字", "が続いた。"]
    assert captions[0].end_ms == captions[1].start_ms
