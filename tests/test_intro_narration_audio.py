"""Real FFmpeg regression: every section intro must carry narration, not source-video audio."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import wave
from array import array

import pytest

from PIL import Image

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.render import RenderService, RenderSettings
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService


class ToneProvider:
    provider_id = "tone"

    def __init__(self, duration: float = 1.5):
        self.duration = duration

    def synthesize(self, text: str, settings: TTSSettings, output_path: Path) -> None:
        rate = settings.sample_rate
        frames = round(self.duration * rate)
        with wave.open(str(output_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(b"".join(int(8500 * math.sin(2 * math.pi * 330 * index / rate)).to_bytes(
                2, "little", signed=True) for index in range(frames)))


def _samples(ffmpeg: str, output: Path, start: float, duration: float) -> list[int]:
    raw = subprocess.run([ffmpeg, "-v", "error", "-ss", str(start), "-i", str(output),
        "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    return [int.from_bytes(raw[index:index + 2], "little", signed=True)
            for index in range(0, len(raw) - 1, 2)]


def _rms(samples: list[int]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) if samples else 0.0


def test_voice_is_audible_during_all_section_intros(tmp_path):
    _render_and_check_intros(tmp_path, segments_per_section=1, duration=1.5)


def test_p2_intro_voice_with_36_segments(tmp_path):
    _render_and_check_intros(tmp_path, segments_per_section=9, duration=0.4)


def test_rendered_p2_intro_without_voice_is_rejected(tmp_path):
    info, project, timeline, output = _render_and_check_intros(tmp_path, segments_per_section=1, duration=1.5)
    p2 = next(item for item in timeline.intros if item.section == SectionName.P2)
    muted = tmp_path / "p2-muted.mp4"
    start = p2.start_ms / 1000
    end = (p2.end_ms + 300) / 1000
    filter_audio = f"volume=0:enable='between(t,{start},{end})'"
    subprocess.run([info["ffmpeg"], "-v", "error", "-y", "-i", str(output),
        "-af", filter_audio, "-c:v", "copy", "-c:a", "aac", str(muted)],
        capture_output=True, check=True)
    with pytest.raises(ValueError, match="Intro P2: video MP4 thiếu lời đọc"):
        RenderService()._verify_intro_narration(project, timeline, muted, info["ffmpeg"])


def test_intro_audio_check_rejects_missing_voice_even_with_other_audio():
    source = array("h", [round(8500 * math.sin(2 * math.pi * 330 * i / 8000)) for i in range(2000)])
    intro_audio = array("h", [round(8500 * math.sin(2 * math.pi * 1000 * i / 8000)) for i in range(2080)])
    assert not RenderService._similar_voice(source, intro_audio)
    assert RenderService._similar_voice(source, array("h", source.tolist() + [0] * 80))


def _render_and_check_intros(tmp_path, *, segments_per_section: int, duration: float):
    info = RenderService.check_ffmpeg()
    library = tmp_path / "media"
    library.mkdir()
    for section in SectionName:
        Image.new("RGB", (320, 180), "blue").save(library / f"{section.value}.png")
        intro = library / f"intro-{section.value}.mp4"
        subprocess.run([info["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=0.6",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.6", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(intro)],
            capture_output=True, check=True)
    directory = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(directory, "P1-P4 intro narration")
    project.tts_settings.provider = "tone"
    project.tts_settings.voice_id = "synthetic-330hz"
    number = 0
    for section in SectionName:
        for _ in range(segments_per_section):
            number += 1
            manager.add_segment(project, section, number, f"第{number}部の語り。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    assets = {item.filename: item for item in project.media}
    assignments = MediaAssignmentService()
    for segment in project.segments:
        media.assign_section_intro(project, segment.section, assets[f"intro-{segment.section.value}.mp4"].id)
        assignments.assign_media(project, segment.id, assets[f"{segment.section.value}.png"].id)
    manager.save(directory, project)
    speech = SentenceSpeechService(TTSService({"tone": ToneProvider(duration)}))
    for segment in project.segments:
        speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    timeline, _ = TimelineService().build_timeline(project, directory, intro_overlap=True,
        effect_sequence=("none",))
    assert all(intro.start_ms == next(block.start_ms for block in timeline.blocks
               if block.section == intro.section) for intro in timeline.intros)
    output = RenderService().render_preview(project, timeline, directory,
        settings=RenderSettings(960, 540, 30, 28, "veryfast"))
    assert output.is_file()
    for intro in timeline.intros:
        start = intro.start_ms / 1000
        during = _rms(_samples(info["ffmpeg"], output, start + 0.12, 0.25))
        after = _rms(_samples(info["ffmpeg"], output, start + 0.9, 0.25))
        assert during > 1500, f"{intro.section.value} intro narration is silent: RMS={during}"
        assert after > 1500, f"{intro.section.value} narration stopped after intro: RMS={after}"
        assert during / after > 0.5, f"{intro.section.value} narration drops during intro"
    return info, project, timeline, output
