"""Full FFmpeg path with P2 beginning at 49.429 s and a 4.5 s intro."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import wave

from PIL import Image
import pytest

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.render import RenderService, RenderSettings
from te.render.service import NarrationVerificationError
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService


class BoundaryVoice:
    provider_id = "boundary-voice"
    durations = {"P1": 49.429, "P2": 8.0, "P3": 2.0, "P4": 2.0}

    def synthesize(self, text, settings, output_path):
        section = text[:2]
        duration = (9.429 if text.startswith("P1-9") else 5.0
                    if section == "P1" else self.durations[section])
        rate = settings.sample_rate
        frequency = 220 + 110 * int(section[1])
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            for offset in range(0, round(duration * rate), rate):
                count = min(rate, round(duration * rate) - offset)
                output.writeframes(b"".join(round(8000 * math.sin(2 * math.pi * frequency *
                    (offset + index) / rate)).to_bytes(2, "little", signed=True)
                    for index in range(count)))


def test_p2_49429ms_intro_and_all_section_audio_boundaries(tmp_path):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    library = tmp_path / "library"
    library.mkdir()
    for section in SectionName:
        Image.new("RGB", (160, 90), "blue").save(library / f"{section.value}.png")
        length = 4.5 if section == SectionName.P2 else 0.6
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=red:s=160x90:r=10:d={length}", "-f", "lavfi", "-i",
            f"sine=frequency=1000:duration={length}", "-shortest", "-c:v", "libx264",
            "-c:a", "aac", str(library / f"intro-{section.value}.mp4")],
            capture_output=True, check=True)
    directory = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(directory, "Long P1 then P2")
    project.tts_settings.provider = "boundary-voice"
    number = 0
    for section in SectionName:
        count = 9 if section == SectionName.P1 else 1
        for index in range(1, count + 1):
            number += 1
            manager.add_segment(project, section, number, f"{section.value}-{index} の語り。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    assets = {asset.filename: asset for asset in project.media}
    for segment in project.segments:
        media.assign_section_intro(project, segment.section,
                                   assets[f"intro-{segment.section.value}.mp4"].id)
        MediaAssignmentService().assign_media(project, segment.id,
            assets[f"{segment.section.value}.png"].id)
    manager.save(directory, project)
    speech = SentenceSpeechService(TTSService({"boundary-voice": BoundaryVoice()}))
    for segment in project.segments:
        speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    timeline, _ = TimelineService().build_timeline(project, directory,
        intro_overlap=True, effect_sequence=("none",))
    p2 = next(block for block in timeline.blocks if block.section == SectionName.P2)
    p2_intro = next(intro for intro in timeline.intros if intro.section == SectionName.P2)
    assert p2.start_ms == p2_intro.start_ms == 49429
    assert p2_intro.end_ms == 53929

    output = RenderService().render_preview(project, timeline, directory,
        settings=RenderSettings(320, 180, 10, 28, "ultrafast"))
    assert output.is_file()
    plan = json.loads((directory / "generated/cache/render/finalizing-plan.json").read_text(encoding="utf-8"))
    assert "concat=n=12:v=0:a=1[narration]" in plan["filter_complex"]
    assert "[0:a]adelay=" not in plan["filter_complex"]
    assert "-c:a" in plan["mux_argv"] and "copy" in plan["mux_argv"]
    for intro in timeline.intros:
        block = next(block for block in timeline.blocks if block.section == intro.section)
        assert intro.start_ms == block.start_ms
        source = RenderService._pcm_slice(ffmpeg, Path(block.audio_path), 100, 500)
        rendered = RenderService._pcm_slice(ffmpeg, output, block.start_ms + 100, 700)
        score, _ = RenderService._voice_score(source, rendered, 0, 800)
        assert score > 0.3, f"{intro.section.value} narration displaced at {block.start_ms} ms"
    for offset in (200, 800, 2500, 4200, 4800):
        sample = RenderService._pcm_slice(ffmpeg, output, p2.start_ms + offset, 300)
        assert RenderService._rms(sample) > 1000, f"P2 unexpectedly silent at +{offset} ms"
    intro_audio = RenderService._pcm_slice(ffmpeg, Path(assets["intro-P2.mp4"].path), 200, 500)
    p2_audio = RenderService._pcm_slice(ffmpeg, output, p2.start_ms + 200, 700)
    assert RenderService._voice_score(intro_audio, p2_audio, 0, 800)[0] < 0.12

    # Recreate the reported 49.429–53.929 s gap after a correct mix. Verification
    # must reject this genuine output loss even though the P2 WAV is active.
    muted = tmp_path / "p2-gap.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(output),
        "-af", "volume=0:enable='between(t,49.429,53.929)'", "-c:v", "copy",
        "-c:a", "aac", str(muted)], capture_output=True, check=True)
    assert RenderService._rms(RenderService._pcm_slice(
        ffmpeg, Path(p2.audio_path), 200, 700)) > 1000
    for offset in (200, 1500, 3000, 4200):
        assert RenderService._rms(RenderService._pcm_slice(
            ffmpeg, muted, p2.start_ms + offset, 300)) < 100
    with pytest.raises(NarrationVerificationError) as failure:
        RenderService()._verify_intro_narration(project, timeline, muted, ffmpeg)
    assert failure.value.section == "P2" and failure.value.reason == "output_silent"

    # A P2 input carrying unexpected demuxer PTS must still start at the
    # project timeline boundary. The visual clip cache must remain reusable.
    clips = {path: path.stat().st_mtime_ns for path in
             (directory / "generated/cache/render").glob("*.mp4") if path.name != "visual.mp4"}
    for cached_audio in (directory / "generated/cache/render").glob("audio-*.m4a"):
        cached_audio.unlink()

    class OffsetInputRenderer(RenderService):
        def _run(self, command, stage, log_dir):
            if stage == "audio-mixing":
                command = command.copy()
                path_index = command.index(p2.audio_path)
                command[path_index - 1:path_index - 1] = ["-itsoffset", "4.5"]
            return super()._run(command, stage, log_dir)

    shifted = OffsetInputRenderer().render_preview(project, timeline, directory,
        settings=RenderSettings(320, 180, 10, 28, "ultrafast"), overwrite=True)
    assert RenderService._rms(RenderService._pcm_slice(
        ffmpeg, shifted, p2.start_ms + 200, 300)) > 1000
    assert all(path.stat().st_mtime_ns == timestamp for path, timestamp in clips.items())
