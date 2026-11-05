"""VOICEVOX contract tests use HTTP mocks; an engine is only needed when opted in."""
import io
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
import wave

import pytest

from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.tts import TTSService, VoicevoxError, VoicevoxProvider
from te.tts.__main__ import main


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    return output.getvalue()


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.value


@pytest.fixture
def engine(monkeypatch):
    calls = []
    speakers = [
        {"name": "後鬼", "speaker_uuid": "uuid-3", "styles": [{"name": "ノーマル", "id": 91}]},
        {"name": "青山龍星", "speaker_uuid": "uuid-1", "styles": [{"name": "ノーマル", "id": 31}, {"name": "感情", "id": 32}]},
        {"name": "剣崎雌雄", "speaker_uuid": "uuid-2", "styles": [{"name": "ノーマル", "id": 57}]},
        {"name": "追加の声", "speaker_uuid": "uuid-4", "styles": [{"name": "朗読", "id": 118}]},
    ]

    def urlopen(request, timeout):
        path = urlsplit(request.full_url)
        calls.append((request.get_method(), path, request.data))
        if path.path == "/version":
            value = "0.25.2"
        elif path.path == "/speakers":
            value = speakers
        elif path.path == "/speaker_info":
            value = {"policy": "Speaker-specific usage policy"}
        elif path.path == "/audio_query":
            value = {"speedScale": 1.0, "kana": "original"}
        elif path.path == "/synthesis":
            return Reply(wav_bytes())
        else:
            raise AssertionError(path.path)
        return Reply(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    monkeypatch.setattr("te.tts.voicevox.urlopen", urlopen)
    return calls


def test_discovery_query_synthesis_metadata_and_cli(tmp_path, engine, capsys):
    provider = VoicevoxProvider()
    assert TTSSettings(provider="voicevox").voice_id == "青山龍星"
    settings = TTSSettings(provider="voicevox", voice_id="青山龍星", style="感情",
                           sample_rate=24000, speaking_rate=1.2, pitch=0.05,
                           intonation=1.3, volume=0.8)
    assert [s["name"] for s in provider.list_voices(settings)][:3] == list(provider.preferred_voices)
    assert provider.check_connection(settings) == "0.25.2"
    target = tmp_path / "sample.wav"
    metadata = provider.synthesize("日本語、そのまま。", settings, target)
    assert target.read_bytes() == wav_bytes()
    assert metadata["speaker_uuid"] == "uuid-1" and metadata["style_id"] == 32
    assert metadata["credit"] == "青山龍星"
    assert metadata["license_text"] == "Speaker-specific usage policy"
    query = next(call for call in engine if call[1].path == "/audio_query")
    assert parse_qs(query[1].query)["text"] == ["日本語、そのまま。"]
    synthesis = next(call for call in engine if call[1].path == "/synthesis")
    assert json.loads(synthesis[2].decode("utf-8"))["speedScale"] == 1.2
    assert json.loads(synthesis[2].decode("utf-8"))["pitchScale"] == 0.05
    assert json.loads(synthesis[2].decode("utf-8"))["intonationScale"] == 1.3
    assert json.loads(synthesis[2].decode("utf-8"))["volumeScale"] == 0.8
    assert main(["sample", "--voice", "後鬼", "--output", str(tmp_path / "other.wav")]) == 0
    assert "WAV:" in capsys.readouterr().out
    assert main(["list"]) == 0
    assert main(["check"]) == 0


def test_project_presets_stale_and_migration(tmp_path, engine):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "VOICEVOX")
    segment = manager.add_segment(project, SectionName.P1, 1, "日本語。")
    settings = TTSSettings(provider="voicevox", voice_id="青山龍星", sample_rate=24000)
    service = TTSService()
    service.save_voice_preset(project, folder, "baseball", settings)
    service.apply_voice_preset(project, folder, "baseball")
    record = service.generate_segment(project, folder, segment.id)
    assert record.status == "ready" and record.duration_seconds == 1
    assert record.credit == "青山龍星" and record.license_reference.endswith("speaker_uuid=uuid-1")
    assert manager.open(folder).audio_records[0].style_id == 31
    for changed in ("voice_id", "style", "speaking_rate", "pitch", "intonation", "volume", "sample_rate", "provider"):
        original = getattr(project.tts_settings, changed)
        setattr(project.tts_settings, changed, "後鬼" if changed == "voice_id" else
                "感情" if changed == "style" else "fake" if changed == "provider" else original + 0.01)
        assert service.detect_stale_audio(project, segment.id), changed
        setattr(project.tts_settings, changed, original)
    project.segments[0].japanese_text += "追記"
    assert service.detect_stale_audio(project, segment.id)
    old = project.to_dict()
    old["schema_version"] = 5
    old.pop("voice_presets")
    for item in old["segments"]:
        item.pop("tts_override")
    for key in ("pitch", "intonation", "volume", "host", "port"):
        old["tts_settings"].pop(key)
        old["audio_records"][0]["settings"].pop(key)
    for key in ("speaker_uuid", "style_id", "credit", "license_reference", "license_text"):
        old["audio_records"][0].pop(key)
    (folder / "project.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    migrated = manager.open(folder)
    assert migrated.schema_version == 7 and migrated.audio_records[0].credit is None


def test_unknown_voice_and_connection_errors(tmp_path, engine, monkeypatch):
    provider = VoicevoxProvider()
    settings = TTSSettings(provider="voicevox", voice_id="unknown")
    with pytest.raises(VoicevoxError, match="not installed"):
        provider.synthesize("文", settings, tmp_path / "x.wav")
    settings.voice_id = "青山龍星"
    settings.style = "not a style"
    with pytest.raises(VoicevoxError, match="unavailable"):
        provider.synthesize("文", settings, tmp_path / "x.wav")
    monkeypatch.setattr("te.tts.voicevox.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("refused")))
    with pytest.raises(VoicevoxError, match="Cannot connect"):
        provider.check_connection(settings)


def test_segment_preset_and_failure_isolation(tmp_path, engine, monkeypatch):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "Two voices")
    first = manager.add_segment(project, SectionName.P1, 1, "一文目。")
    second = manager.add_segment(project, SectionName.P1, 2, "二文目。")
    service = TTSService()
    project.tts_settings = TTSSettings(provider="voicevox", voice_id="青山龍星", sample_rate=24000)
    service.save_voice_preset(project, folder, "guest", TTSSettings(provider="voicevox", voice_id="後鬼", sample_rate=24000))
    service.apply_voice_preset(project, folder, "guest", second.id)
    assert [item.status for item in service.generate_all(project, folder)] == ["ready", "ready"]
    assert [item.credit for item in project.audio_records] == ["青山龍星", "後鬼"]
    assert service.get_audio_status(project, second.id) == "ready"
    old = Path(project.audio_records[0].path).read_bytes()

    def disconnected(*_args, **_kwargs):
        raise URLError("refused")

    monkeypatch.setattr("te.tts.voicevox.urlopen", disconnected)
    failed = service.regenerate_segment(project, folder, second.id)
    assert failed.status == "failed" and "Cannot connect" in failed.error
    assert service.get_audio_status(project, first.id) == "ready"
    assert Path(project.audio_records[0].path).read_bytes() == old


@pytest.mark.skipif(os.environ.get("TE_VOICEVOX_INTEGRATION") != "1", reason="opt-in local engine test")
def test_real_local_engine(tmp_path):
    settings = TTSSettings(provider="voicevox", voice_id="青山龍星", sample_rate=24000)
    provider = VoicevoxProvider()
    assert provider.check_connection(settings)
    path = tmp_path / "real.wav"
    metadata = provider.synthesize("今日は素晴らしい試合です。", settings, path)
    with wave.open(str(path), "rb") as audio:
        assert audio.getnframes() > 0
    assert metadata["credit"] == "青山龍星"
