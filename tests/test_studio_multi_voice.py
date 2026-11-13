"""Deterministic B2 tests without VOICEVOX, GPT or a desktop display."""
from array import array
import json
from pathlib import Path
import wave

import pytest

from te.project.models import SectionName
from te.studio.ai_speakers import script_fingerprint
from te.studio.multi_voice import MultiVoiceService, speech_plan
from te.studio.rendering import StudioRenderService
from te.studio.service import StudioService
from te.tts import SentenceSpeechService, TTSService


class FakeVoices:
    calls: list[tuple[str, str, float]] = []
    names = ("A", "B", "C", "D")

    def __init__(self, timeout=30):
        pass

    def check_connection(self, _settings):
        return "test-engine-1"

    def list_voices(self, _settings):
        return [{"name": role, "speaker_uuid": role,
                 "styles": [{"id": index, "name": "ノーマル"}]}
                for index, role in enumerate(self.names, 1)]

    def synthesize(self, text, settings, output, *, style_id=None):
        assert style_id is None or style_id == self.names.index(settings.voice_id) + 1
        self.calls.append((text, settings.voice_id, settings.speaking_rate))
        amplitude = (self.names.index(settings.voice_id) + 1) * 1000
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(settings.sample_rate)
            stream.writeframes(array("h", [amplitude] * (2400 * len(text))).tobytes())
        return {"speaker_uuid": settings.voice_id, "style_id": self.names.index(settings.voice_id) + 1}


def project(tmp_path, japanese="始「話」終。", spans=((0, 1, "A"), (1, 4, "B"), (4, 6, "A"))):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("B2 fixture")
    service.set_section(state, SectionName.P1, japanese=japanese, vietnamese="Một.")
    state.project.tts_settings.provider = "voicevox"
    state.project.tts_settings.voice_id = "A"
    state.project.tts_settings.style = "ノーマル"
    state.project.tts_settings.sample_rate = 24000
    state.speaker_data["assignments"] = [
        {"section": "P1", "role": role, "vietnamese": [0, 4], "japanese": [start, end],
         "confidence": "high", "warning": "", "confirmed": True, "source": "manual"}
        for start, end, role in spans]
    state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
    state.speaker_data["status"] = "confirmed"
    for index, role in enumerate(FakeVoices.names, 1):
        state.speaker_data["voices"][role] = {"speaker_uuid": role, "style_id": index,
                                               "speaker": role, "style": "ノーマル"}
    state.narration_mode = "multi"
    service.save_project(state)
    return service, state


def engine(service):
    fake = FakeVoices()
    speech = SentenceSpeechService(TTSService(providers={"voicevox": fake}))
    return StudioRenderService(service, speech=speech, multi_voice=MultiVoiceService(fake))


def samples(path: Path):
    with wave.open(str(path), "rb") as stream:
        return array("h", stream.readframes(stream.getnframes()))


def test_multiple_roles_within_one_sentence_keep_audio_order_and_sentence_anchor(tmp_path):
    FakeVoices.calls.clear()
    service, state = project(tmp_path)
    plan, warnings = speech_plan(state)
    unit = state.project.sentence_units[0]
    assert plan[unit.id] == [("A", "始"), ("B", "「話」"), ("A", "終。")]
    assert not warnings
    assert engine(service).generate_narration(state) == 1
    record = state.project.sentence_audio_records[0]
    audio = samples(Path(record.path))
    assert audio[0] == 1000 and audio[2400] == 2000 and audio[9600] == 1000
    assert len(audio) == 14400
    assert len(state.project.subtitle_captions) == 1
    assert state.project.subtitle_captions[0].text == "始「話」終。"
    assert state.project.subtitle_captions[0].sentence_id == unit.id
    assert [item[1] for item in FakeVoices.calls] == ["A", "B", "A"]
    assert state.audio_mode == "multi"
    assert service.open_project(state.directory).narration_mode == "multi"


def test_cache_reuses_unaffected_roles_and_inherits_parameters(tmp_path):
    FakeVoices.calls.clear()
    service, state = project(tmp_path)
    renderer = engine(service)
    renderer.generate_narration(state)
    assert len(FakeVoices.calls) == 3
    renderer.generate_narration(state)
    assert len(FakeVoices.calls) == 3
    state.speaker_data["parameters"]["B"] = {"speaking_rate": 1.25, "pitch": 0.03,
                                                "intonation": 1.1, "volume": 0.9}
    service.save_project(state)
    renderer.generate_narration(state)
    assert len(FakeVoices.calls) == 4
    assert FakeVoices.calls[-1] == ("「話」", "B", 1.25)
    assert service.open_project(state.directory).speaker_data["parameters"]["B"]["pitch"] == 0.03


def test_a_c_a_d_a_and_reused_voice_style(tmp_path):
    FakeVoices.calls.clear()
    source = "あ「し」い「で」う。"
    spans = ((0, 1, "A"), (1, 4, "C"), (4, 5, "A"), (5, 8, "D"), (8, 10, "A"))
    service, state = project(tmp_path, source, spans)
    state.speaker_data["voices"]["D"] = state.speaker_data["voices"]["C"].copy()
    service.save_project(state)
    plan, _ = speech_plan(state)
    assert [role for role, _ in next(iter(plan.values()))] == ["A", "C", "A", "D", "A"]
    engine(service).generate_narration(state)
    assert [voice for _, voice, _ in FakeVoices.calls] == ["A", "C", "A", "C", "A"]
    assert "".join(text for text, _, _ in FakeVoices.calls) == source


@pytest.mark.parametrize("roles", [("A",), ("A", "B"), ("A", "C"),
                                   ("A", "C", "D"), ("A", "B", "C", "D")])
def test_partial_role_sets_and_missing_sections(tmp_path, roles):
    japanese = "".join(f"{role}。" for role in roles)
    spans = tuple((index * 2, index * 2 + 2, role) for index, role in enumerate(roles))
    service, state = project(tmp_path, japanese, spans)
    assert {role for parts in speech_plan(state)[0].values() for role, _ in parts} == set(roles)
    assert engine(service).generate_narration(state) == len(roles)
    assert len(state.project.segments) == 1


def test_stale_and_overlapping_attribution_rejected_before_synthesis(tmp_path):
    FakeVoices.calls.clear()
    service, state = project(tmp_path)
    state.speaker_data["assignments"][1]["japanese"] = [0, 4]
    service.save_project(state)
    with pytest.raises(ValueError, match="chồng lấn"):
        engine(service).generate_narration(state)
    assert FakeVoices.calls == []
    state.speaker_data["status"] = "stale"
    with pytest.raises(ValueError, match="đã cũ"):
        engine(service).generate_narration(state)


def test_unassigned_text_uses_disclosed_a_fallback_and_missing_voice_fails(tmp_path):
    FakeVoices.calls.clear()
    service, state = project(tmp_path)
    state.speaker_data["assignments"] = state.speaker_data["assignments"][1:2]
    service.save_project(state)
    plan, warnings = speech_plan(state)
    assert [role for role, _ in next(iter(plan.values()))] == ["A", "B", "A"]
    assert warnings and "giọng A" in warnings[0]
    state.speaker_data["voices"].pop("B")
    service.save_project(state)
    with pytest.raises(ValueError, match="Thiếu giọng B"):
        engine(service).generate_narration(state)
    assert FakeVoices.calls == []


def test_switch_back_to_single_voice_regenerates_sentence(tmp_path, monkeypatch):
    import te.studio.rendering as rendering
    FakeVoices.calls.clear()
    service, state = project(tmp_path)
    renderer = engine(service)
    renderer.generate_narration(state)
    state.narration_mode = "single"
    service.save_project(state)
    monkeypatch.setattr(rendering, "VoicevoxProvider", FakeVoices)
    renderer.generate_narration(state)
    assert state.audio_mode == "single"
    assert state.multi_voice_manifest == {}
    assert FakeVoices.calls[-1][0] == "始「話」終。"
    assert len(set(samples(Path(state.project.sentence_audio_records[0].path)))) == 1


def test_multi_voice_timeline_keeps_key_intro_and_section_boundary(tmp_path):
    from PIL import Image
    from te.timeline import TimelineService
    from te.render import RenderService
    import subprocess

    service, state = project(tmp_path, "A。B。", ((0, 2, "A"), (2, 4, "B")))
    service.set_section(state, SectionName.P2, japanese="後半。", vietnamese="Phần hai.")
    state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
    service.save_project(state)
    media = tmp_path / "media"
    media.mkdir()
    Image.new("RGB", (320, 180), (50, 100, 150)).save(media / "normal.png")
    service.select_media_folder(state, media)
    key_path = tmp_path / "key.png"
    Image.new("RGB", (320, 180), (250, 40, 100)).save(key_path)
    draft = service.add_key_draft(state, key_path)
    vi = service.vietnamese_units(state, SectionName.P1)[0]
    ja = service.japanese_choices(state, SectionName.P1)[1]
    service.confirm_key_draft(state, draft["id"], section=SectionName.P1,
                              vietnamese_key=vi.key, sentence_id=ja.sentence_id)
    intro = tmp_path / "intro.mp4"
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=blue:s=320x180:r=30:d=0.15", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(intro)], capture_output=True, check=True)
    service.set_intro(state, SectionName.P1, intro)
    renderer = engine(service)
    assert renderer.generate_narration(state) == 3
    timeline = renderer.prepare_timeline(state)
    key = state.project.key_placements[0]
    reveal = next(item for item in timeline.placements if item.id == key.id)
    assert reveal.start_ms == TimelineService.get_sentence_range(state.project, timeline, key.sentence_id)[0]
    assert timeline.intros[0].start_ms == timeline.blocks[0].start_ms == 0
    assert timeline.blocks[1].start_ms >= timeline.blocks[0].end_ms
    assert [item.text for item in state.project.subtitle_captions] == ["A。", "B。", "後半。"]
    output = renderer.render_video(state, preview=True)
    assert output.is_file() and output.stat().st_size > 0
    performance = sorted((state.directory / "generated/output/diagnostics").glob("performance-*.json"))[-1]
    report = json.loads(performance.read_text(encoding="utf-8"))
    assert report["narration_mode"] == "multi" and report["status"] == "success"
    assert report["counts"]["wav_cache_hits"] >= 3
    RenderService._verify_packet_timestamps(RenderService.check_ffmpeg()["ffprobe"], output)
    state.speaker_data["parameters"]["B"] = {"speaking_rate": 1.3, "pitch": 0.0,
                                                "intonation": 1.0, "volume": 1.0}
    service.save_project(state)
    with pytest.raises(ValueError, match="Giọng hoặc phân vai đã đổi"):
        renderer.prepare_timeline(state)
    renderer.generate_narration(state)
    assert renderer.prepare_timeline(state).blocks
