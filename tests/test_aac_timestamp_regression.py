"""Real FFmpeg regression for the reported 49.429 s / 270.427 s two-section render."""
from __future__ import annotations

import math
import json
from pathlib import Path
import subprocess
import wave

from PIL import Image
import pytest

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.render import RenderService, RenderSettings
from te.render.service import RenderError
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService


class LongVoice:
    provider_id = "long-test-voice"

    def synthesize(self, text, settings, output_path):
        duration = 49.429 if text.startswith("P1") else 270.427
        rate = 8000
        total = round(duration * rate)
        phase = 0.0
        with wave.open(str(output_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            for offset in range(0, total, rate):
                data = bytearray()
                for index in range(offset, min(offset + rate, total)):
                    seconds = index / rate
                    frequency = (240 if text.startswith("P1") else 330) + 60 * math.sin(2 * math.pi * 0.37 * seconds)
                    phase += 2 * math.pi * frequency / rate
                    value = round(6500 * math.sin(phase) + 1400 * math.sin(2.2 * phase))
                    data.extend(value.to_bytes(2, "little", signed=True))
                stream.writeframes(data)


def test_video_filter_reconfiguration_cannot_reset_aac_timestamps(tmp_path):
    """Reproduce the old combined-graph failure, then verify independent streams."""
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    clips = []
    for index, color_space in enumerate(("bt709", "smpte170m")):
        clip = tmp_path / f"clip-{index}.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={'blue' if index == 0 else 'red'}:s=160x90:r=10:d=5",
            "-vf", f"setparams=range=tv:color_primaries={color_space}:"
                   f"color_trc={color_space}:colorspace={color_space}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_primaries", color_space,
            "-color_trc", color_space, "-colorspace", color_space, str(clip)],
            capture_output=True, check=True)
        clips.append(clip)
    list_file = tmp_path / "concat.txt"
    list_file.write_text("".join(f"file '{clip}'\n" for clip in clips), encoding="utf-8")
    visual = tmp_path / "visual.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(visual)], capture_output=True, check=True)
    wavs = []
    for index in range(2):
        wav = tmp_path / f"voice-{index}.wav"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency={330 + index * 50}:duration=5", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, check=True)
        wavs.append(wav)
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 160\nPlayResY: 90\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
        "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,DejaVu Sans,14,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        "Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,Test\n", encoding="utf-8")
    audio_graph = ("[1:a]aresample=48000,asetpts=PTS-STARTPTS[a1];"
                   "[2:a]aresample=48000,asetpts=PTS-STARTPTS[a2];"
                   "[a1][a2]concat=n=2:v=0:a=1[a]")
    old = tmp_path / "old-combined.mp4"
    combined = [ffmpeg, "-hide_banner", "-y", "-i", str(visual), "-i", str(wavs[0]),
        "-i", str(wavs[1]), "-filter_complex",
        f"[0:v]ass=filename='{ass}'[v];{audio_graph}", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-c:a", "aac", "-t", "10", str(old)]
    with pytest.raises(RenderError, match="Render failed during muxing"):
        RenderService()._run(combined, "muxing", tmp_path)
    old_log = (tmp_path / "muxing.log").read_text(encoding="utf-8")
    assert "Reconfiguring filter graph" in old_log
    assert "Queue input is backward in time" in old_log
    assert "Non-monotonic DTS" in old_log
    assert RenderService._rms(RenderService._pcm_slice(ffmpeg, old, 5500, 400)) < 100

    audio = tmp_path / "separate-audio.m4a"
    video = tmp_path / "separate-video.mp4"
    finished = tmp_path / "separate-final.mp4"
    RenderService()._run([ffmpeg, "-y", "-i", str(wavs[0]), "-i", str(wavs[1]),
        "-filter_complex", audio_graph.replace("[1:a]", "[0:a]").replace("[2:a]", "[1:a]"),
        "-map", "[a]", "-c:a", "aac", str(audio)], "audio-mixing", tmp_path)
    RenderService()._run([ffmpeg, "-y", "-i", str(visual), "-an", "-vf",
        f"ass=filename='{ass}'", "-c:v", "libx264", str(video)], "finalizing", tmp_path)
    RenderService()._run([ffmpeg, "-y", "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(finished)],
        "muxing", tmp_path)
    RenderService._verify_packet_timestamps(ffprobe, finished)
    RenderService._verify_packet_timestamps(ffprobe, finished, "v:0")
    assert RenderService._rms(RenderService._pcm_slice(ffmpeg, finished, 5500, 400)) > 1000


def test_reported_duration_render_has_monotonic_aac_and_aligned_p2(tmp_path):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    library = tmp_path / "library"
    library.mkdir()
    for section in (SectionName.P1, SectionName.P2):
        Image.new("RGB", (160, 90), "blue").save(library / f"{section.value}.png")
    intro = library / "intro-P2.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=30:d=16.2", "-f", "lavfi", "-i",
        "sine=frequency=1000:duration=16.2", "-shortest", "-c:v", "libx264",
        "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
        "-colorspace", "smpte170m", "-c:a", "aac", str(intro)],
        capture_output=True, check=True)

    directory = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(directory, "AAC DTS boundary")
    project.tts_settings.provider = "long-test-voice"
    manager.add_segment(project, SectionName.P1, 1, "P1 の語り。")
    manager.add_segment(project, SectionName.P2, 2, "P2 の語り。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    assets = {asset.filename: asset for asset in project.media}
    assets["intro-P2.mp4"].duration_seconds = 16.161
    media.assign_section_intro(project, SectionName.P2, assets["intro-P2.mp4"].id)
    for segment in project.segments:
        MediaAssignmentService().assign_media(project, segment.id,
            assets[f"{segment.section.value}.png"].id)
    manager.save(directory, project)
    speech = SentenceSpeechService(TTSService({"long-test-voice": LongVoice()}))
    for segment in project.segments:
        speech.sync_segment(project, directory, segment.id, rebuild_timeline=False)
    timeline, _ = TimelineService().build_timeline(project, directory,
        intro_overlap=True, effect_sequence=("none",))
    assert [(block.start_ms, block.end_ms) for block in timeline.blocks] == [(0, 49429), (49429, 319856)]
    assert [(item.start_ms, item.end_ms) for item in timeline.intros] == [(49429, 65590)]

    output = RenderService().render_preview(project, timeline, directory,
        settings=RenderSettings(160, 90, 2, 28, "ultrafast"))
    assert output.is_file()
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=start_time,duration", "-of", "json", str(output)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert abs(float(probe["streams"][0]["start_time"])) < 0.01
    assert abs(float(probe["streams"][0]["duration"]) - 319.856) < 0.03
    RenderService._verify_packet_timestamps(ffprobe, output)
    RenderService._verify_packet_timestamps(ffprobe, output, "v:0")
    cache = directory / "generated/cache/render"
    for name in ("audio-mixing.log", "muxing.log"):
        log = (cache / name).read_text(encoding="utf-8").casefold()
        assert "non-monotonic dts" not in log
        assert "queue input is backward in time" not in log
    p2 = timeline.blocks[1]
    for offset in (300, 5000, 15500, 120000, 269000, 269700):
        source = RenderService._pcm_slice(ffmpeg, Path(p2.audio_path), offset, 800)
        rendered = RenderService._pcm_slice(ffmpeg, output, p2.start_ms + offset, 1000)
        assert RenderService._rms(rendered) > 500
        score, _ = RenderService._voice_score(source, rendered, 0, 800)
        assert score > 0.35, f"P2 WAV does not match MP4 near {p2.start_ms + offset} ms: {score}"
    p1_tail = RenderService._pcm_slice(ffmpeg, output, 48600, 600)
    p2_head = RenderService._pcm_slice(ffmpeg, output, 49600, 600)
    assert RenderService._rms(p1_tail) > 500 and RenderService._rms(p2_head) > 500
