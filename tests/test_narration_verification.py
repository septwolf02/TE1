"""AAC narration verification with timing shifts, music and real missing speech."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import wave

import pytest
from PIL import Image

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.render import RenderService, RenderSettings
from te.render.service import NarrationVerificationError
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService


def _speech_like_wav(path: Path, duration: float = 6.0) -> None:
    """Nonperiodic voiced signal so AAC timing tests cannot pass by tone phase coincidence."""
    rate = 16000
    phase = 0.0
    frames = bytearray()
    for index in range(round(duration * rate)):
        seconds = index / rate
        frequency = 210 + 85 * math.sin(2 * math.pi * 0.71 * seconds)
        phase += 2 * math.pi * frequency / rate
        sample = round(6500 * math.sin(phase) + 1600 * math.sin(2.3 * phase))
        frames.extend(sample.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


def _aac_fixture(tmp_path):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    source = tmp_path / "narration.wav"
    _speech_like_wav(source)
    base = tmp_path / "base.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=160x90:r=15:d=6", "-i", str(source), "-shortest",
        "-c:v", "libx264", "-c:a", "aac", str(base)], capture_output=True, check=True)
    section = SectionName.P2
    project = SimpleNamespace(segments=[SimpleNamespace(id="seg", tts_override=None)],
                              tts_settings=TTSSettings(provider="tone"))
    timeline = SimpleNamespace(intros=[SimpleNamespace(section=section, start_ms=0, end_ms=5200)],
        blocks=[SimpleNamespace(segment_id="seg", section=section, start_ms=0, end_ms=6000,
                                audio_path=str(source))])
    return ffmpeg, base, project, timeline


def _remix(ffmpeg, base, target, audio_filter):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(base),
        "-f", "lavfi", "-i", "sine=frequency=710:duration=6",
        "-filter_complex", audio_filter, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", str(target)], capture_output=True, check=True)


def test_aac_delay_and_music_do_not_reject_narration(tmp_path):
    ffmpeg, base, project, timeline = _aac_fixture(tmp_path)
    delayed = tmp_path / "delayed-with-music.mp4"
    _remix(ffmpeg, base, delayed,
           "[0:a]adelay=120:all=1,volume=0.75[v];"
           "[1:a]volume=0.35[bg];[v][bg]amix=inputs=2:normalize=0[a]")
    RenderService()._verify_intro_narration(project, timeline, delayed, ffmpeg)
    source = RenderService._pcm_slice(ffmpeg, Path(timeline.blocks[0].audio_path), 1000, 700)
    output = RenderService._pcm_slice(ffmpeg, delayed, 800, 1300)
    score, shift = RenderService._voice_score(source, output, expected=1600)
    assert score > 0.4 and 70 <= shift / 8 <= 180


def test_single_240ms_mismatch_near_4800ms_is_not_fatal(tmp_path):
    ffmpeg, base, project, timeline = _aac_fixture(tmp_path)
    briefly_muted = tmp_path / "briefly-muted.mp4"
    _remix(ffmpeg, base, briefly_muted,
           "[0:a]volume=0:enable='between(t,4.8,5.04)'[v];"
           "[1:a]volume=0.2[bg];[v][bg]amix=inputs=2:normalize=0[a]")
    RenderService()._verify_intro_narration(project, timeline, briefly_muted, ffmpeg)


def test_missing_narration_with_audible_music_is_rejected(tmp_path):
    ffmpeg, base, project, timeline = _aac_fixture(tmp_path)
    music_only = tmp_path / "music-only.mp4"
    _remix(ffmpeg, base, music_only,
           "[0:a]volume=0[v];[1:a]volume=0.4[bg];"
           "[v][bg]amix=inputs=2:normalize=0[a]")
    with pytest.raises(NarrationVerificationError) as failure:
        RenderService()._verify_intro_narration(project, timeline, music_only, ffmpeg)
    assert failure.value.section == "P2"
    assert failure.value.reason == "low_similarity"
    assert failure.value.windows
    assert all(item["output_rms"] > 50 for item in failure.value.windows)


def test_sustained_missing_speech_with_music_is_rejected(tmp_path):
    ffmpeg, base, project, timeline = _aac_fixture(tmp_path)
    dropout = tmp_path / "long-dropout.mp4"
    _remix(ffmpeg, base, dropout,
           "[0:a]volume=0:enable='between(t,2.0,4.3)'[v];"
           "[1:a]volume=0.2[bg];[v][bg]amix=inputs=2:normalize=0[a]")
    with pytest.raises(NarrationVerificationError) as failure:
        RenderService()._verify_intro_narration(project, timeline, dropout, ffmpeg)
    assert failure.value.reason == "low_similarity"
    assert any(item["matched"] for item in failure.value.windows)
    assert any(not item["matched"] for item in failure.value.windows)


def test_failed_final_render_keeps_mp4_audio_report_logs_and_caches(tmp_path):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    library = tmp_path / "media"
    library.mkdir()
    Image.new("RGB", (160, 90), "blue").save(library / "still.png")
    intro = library / "intro-P1.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=15:d=0.6", "-an", "-c:v", "libx264", str(intro)],
        capture_output=True, check=True)
    directory = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(directory, "diagnostic test")
    project.tts_settings.provider = "tone"
    segment = manager.add_segment(project, SectionName.P1, 1, "語りがあります。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    assets = {item.filename: item for item in project.media}
    media.assign_section_intro(project, SectionName.P1, assets[intro.name].id)
    MediaAssignmentService().assign_media(project, segment.id, assets["still.png"].id)
    manager.save(directory, project)
    class ShortSpeechProvider:
        provider_id = "tone"

        def synthesize(self, text, settings, output_path):
            _speech_like_wav(output_path, 1.5)

    SentenceSpeechService(TTSService({"tone": ShortSpeechProvider()})).sync_segment(
        project, directory, segment.id, rebuild_timeline=False)
    timeline, _ = TimelineService().build_timeline(project, directory,
        intro_overlap=True, effect_sequence=("none",))

    class BrokenMixRenderer(RenderService):
        def _run(self, command, stage, log_dir):
            super()._run(command, stage, log_dir)
            if stage == "finalizing":
                partial = Path(command[-1])
                muted = partial.with_name("muted.mp4")
                subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(partial),
                    "-af", "volume=0", "-c:v", "copy", "-c:a", "aac", str(muted)],
                    capture_output=True, check=True)
                os.replace(muted, partial)

    with pytest.raises(ValueError, match="video chẩn đoán"):
        BrokenMixRenderer().render_preview(project, timeline, directory,
            settings=RenderSettings(960, 540, 30, 28, "veryfast"))
    diagnostic = next((directory / "generated/output/diagnostics").iterdir())
    assert (diagnostic / "suspected-narration.mp4").is_file()
    assert (diagnostic / "suspected-interval.wav").is_file()
    assert (diagnostic / "ffmpeg-finalizing.log").is_file()
    plan = json.loads((diagnostic / "finalizing-plan.json").read_text(encoding="utf-8"))
    assert plan["audio_blocks"][0]["start_ms"] == plan["intros"][0]["start_ms"] == 0
    assert "concat=" in plan["filter_complex"] and "[narration]" in plan["filter_complex"]
    report = json.loads((diagnostic / "narration-verification.json").read_text(encoding="utf-8"))
    assert report["section"] == "P1" and report["reason"] == "output_silent"
    assert report["windows"] and report["source_wavs"]
    assert list((directory / "generated/cache/render").glob("*.mp4"))
    assert Path(project.audio_records[0].path).is_file()
