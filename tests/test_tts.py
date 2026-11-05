import json
from pathlib import Path
import wave

import jsonschema
import pytest

from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.tts import FakeTTSProvider, TTSService


def project_with_segments(tmp_path, count=3):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "TTS")
    for number in range(1, count + 1):
        section = list(SectionName)[min(3, (number - 1) * 4 // count)]
        manager.add_segment(project, section, number, f"日本語の文{number}。")
    manager.save(folder, project)
    return manager, folder, project


def test_one_segment_exact_text_duration_and_save(tmp_path):
    manager, folder, project = project_with_segments(tmp_path)
    segment = project.segments[0]
    service = TTSService()
    record = service.generate_segment(project, folder, segment.id)
    assert record.status == "ready"
    assert record.segment_id == segment.id and record.path == segment.audio_ref
    assert record.original_text == segment.japanese_text == record.provider_input_text
    assert record.duration_seconds == 1.0
    assert record.provider_id == "fake" and record.voice_id == "test"
    assert record.generation_version == 1 and record.generated_at
    assert Path(record.path).parent == folder / "generated" / "audio"
    with wave.open(record.path, "rb") as audio:
        assert audio.getnframes() == 16000
    manager.save(folder, project)
    reopened = manager.open(folder)
    assert reopened.audio_records[0].segment_id == segment.id
    assert service.get_audio_status(reopened, segment.id) == "ready"
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/te/schemas/project-v7.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((folder / "project.json").read_text(encoding="utf-8")), schema)


def test_43_segments_generate_and_regenerate_one(tmp_path):
    _, folder, project = project_with_segments(tmp_path, 43)
    service = TTSService()
    records = service.generate_all(project, folder)
    assert len(records) == 43
    assert len({r.segment_id for r in records}) == 43
    assert all(r.status == "ready" and r.duration_seconds > 0 for r in records)
    assert service.generate_all(project, folder) == []
    previous = {r.segment_id: (r.generation_version, Path(r.path).stat().st_mtime_ns) for r in records}
    changed = service.regenerate_segment(project, folder, project.segments[17].id)
    assert changed.generation_version == 2
    assert all(r.generation_version == previous[r.segment_id][0] for r in project.audio_records if r.segment_id != changed.segment_id)
    assert all(Path(r.path).stat().st_mtime_ns == previous[r.segment_id][1] for r in project.audio_records if r.segment_id != changed.segment_id)


class SelectiveFailureProvider(FakeTTSProvider):
    provider_id = "sometimes-fails"

    def synthesize(self, text, settings, output_path):
        if "文2" in text:
            raise RuntimeError("provider refused segment")
        super().synthesize(text, settings, output_path)


def test_failure_isolation_and_continue_or_stop(tmp_path):
    _, folder, project = project_with_segments(tmp_path)
    project.tts_settings.provider = "sometimes-fails"
    service = TTSService({"sometimes-fails": SelectiveFailureProvider()})
    result = service.generate_all(project, folder, continue_on_error=True)
    assert [r.status for r in result] == ["ready", "failed", "ready"]
    assert "provider refused" in result[1].error
    assert result[1].path is None and project.segments[1].audio_ref is None
    assert service.get_audio_status(project, project.segments[0].id) == "ready"
    stopped = service.regenerate_selected(project, folder, [project.segments[1].id, project.segments[2].id], continue_on_error=False)
    assert len(stopped) == 1 and stopped[0].status == "failed"


def test_failed_regeneration_preserves_previous_wav(tmp_path):
    _, folder, project = project_with_segments(tmp_path)
    segment = project.segments[1]
    good = TTSService().generate_segment(project, folder, segment.id)
    old = Path(good.path).read_bytes()
    bad = TTSService({"sometimes-fails": SelectiveFailureProvider()})
    failed = bad.regenerate_segment(project, folder, segment.id, settings=TTSSettings(provider="sometimes-fails"))
    assert failed.status == "failed" and failed.path == good.path
    assert Path(good.path).read_bytes() == old
    assert bad.get_audio_status(project, segment.id) == "failed"


def test_stale_and_missing_file(tmp_path):
    _, folder, project = project_with_segments(tmp_path)
    segment = project.segments[0]
    service = TTSService()
    record = service.generate_segment(project, folder, segment.id)
    segment.japanese_text += "追記"
    assert service.detect_stale_audio(project, segment.id)
    assert service.get_audio_status(project, segment.id) == "stale"
    refreshed = service.generate_all(project, folder)
    assert len(refreshed) == 3 and refreshed[0].generation_version == 2
    Path(refreshed[0].path).unlink()
    assert service.get_audio_status(project, segment.id) == "missing_file"
    assert record.segment_id == refreshed[0].segment_id


def test_selected_generation_and_settings_override(tmp_path):
    manager, folder, project = project_with_segments(tmp_path)
    service = TTSService()
    selected = [project.segments[2].id, project.segments[0].id]
    records = service.regenerate_selected(project, folder, selected)
    assert [record.segment_id for record in records] == selected
    assert service.get_audio_status(project, project.segments[1].id) == "missing"
    overridden = service.generate_segment(
        project, folder, selected[0],
        settings=TTSSettings(voice_id="alternate", speaking_rate=2.0, sample_rate=8000),
    )
    assert overridden.voice_id == "alternate"
    assert overridden.settings.sample_rate == 8000
    assert overridden.duration_seconds == 0.5
    assert manager.open(folder).audio_records[0].segment_id in selected


@pytest.mark.parametrize("old_version", [1, 2, 3])
def test_project_migrations_preserve_segments(tmp_path, old_version):
    manager, folder, project = project_with_segments(tmp_path, 1)
    data = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    data["schema_version"] = old_version
    data.pop("tts_settings")
    data.pop("audio_records")
    if old_version < 3:
        for key in ("media_library_path", "media_assignments", "section_intros", "media_reuse_distance"):
            data.pop(key)
    if old_version == 1:
        for segment in data["segments"]:
            for key in ("notes", "status", "audio_ref", "caption_refs", "media_placement_refs"):
                segment.pop(key)
    (folder / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    opened = manager.open(folder)
    assert opened.segments[0].id == project.segments[0].id
    assert opened.tts_settings.provider == "fake" and opened.audio_records == []
    manager.save(folder, opened)
    assert json.loads((folder / "project.json").read_text(encoding="utf-8"))["schema_version"] == 7
