import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.render import RenderService, RenderSettings
from te.subtitles import SubtitleService
from te.timeline import TimelineService
from te.tts import TTSService


def test_ffmpeg_dependency_and_missing_font(monkeypatch, tmp_path):
    service = RenderService()
    info = service.check_ffmpeg()
    assert "ffmpeg version" in info["version"]
    with pytest.raises(ValueError, match="font file unavailable"):
        service._font(RenderSettings(font_path=str(tmp_path / "missing.ttf")))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="FFmpeg and ffprobe"):
        service.check_ffmpeg()


def test_real_preview_render(tmp_path):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    library = tmp_path / "library"
    library.mkdir()
    image = library / "still.png"
    intro = library / "intro.mp4"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=0.1", "-frames:v", "1", str(image)],
                   capture_output=True, check=True)
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=0.5", "-c:v", "libx264", str(intro)],
                   capture_output=True, check=True)
    music = tmp_path / "music.wav"
    with wave.open(str(music), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000)
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "Render")
    first = manager.add_segment(project, SectionName.P1, 1, "第一文。")
    manager.add_segment(project, SectionName.P2, 2, "第二文。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    by_name = {asset.filename: asset for asset in project.media}
    media.assign_section_intro(project, SectionName.P1, by_name["intro.mp4"].id)
    MediaAssignmentService().assign_media(project, first.id, by_name["still.png"].id, effect_preference="slow_zoom_in")
    manager.save(folder, project)
    TTSService().generate_all(project, folder)
    SubtitleService().align_all(project, folder)
    timeline_service = TimelineService()
    timeline, _ = timeline_service.build_timeline(project, folder)
    timeline_service.set_transition(timeline, SectionName.P2, "fade")
    timeline_service.assign_section_music(timeline, SectionName.P1, music)
    timeline_service.save_timeline(project, folder, timeline)
    render = RenderService()
    progress = []
    output = render.render_preview(project, timeline, folder, progress=progress.append)
    assert output.is_file() and render.get_render_status() == "completed"
    assert "burning subtitles" in progress and "mixing audio" in progress
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                                      capture_output=True, text=True, check=True).stdout)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"], video["pix_fmt"]) == (960, 540, "h264", "yuv420p")
    assert audio["codec_name"] == "aac"
    assert 2.4 <= float(probe["format"]["duration"]) <= 2.7
    manifest = json.loads((folder / "generated/output/preview.manifest.json").read_text())
    assert manifest["status"] == "completed" and manifest["project_id"] == project.id
    assert manager.open(folder).id == project.id
    with pytest.raises(FileExistsError):
        render.render_preview(project, timeline, folder)
    render.cancel_render()
    render.clear_cache(folder)
    assert not (folder / "generated/cache/render").exists()
