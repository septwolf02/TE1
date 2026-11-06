"""Large Japanese ASS captions must fit a real FFmpeg frame at both output sizes."""
from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from te.project import ProjectManager
from te.render import RenderService, RenderSettings


@pytest.mark.parametrize("length", [50, 100, 220])
@pytest.mark.parametrize("resolution", [(960, 540), (1920, 1080)])
def test_large_caption_split_preserves_every_character(tmp_path, length, resolution):
    project = ProjectManager().create(tmp_path / "project", "subtitles")
    text = ("「勝負はここからだ」と監督が語り、球場は大きく沸いた。" * 12)[:length]
    caption = SimpleNamespace(id="cap", text=text, start_ms=0, end_ms=12000)
    timeline = SimpleNamespace(captions=[caption], headline={})
    settings = RenderSettings(width=resolution[0], height=resolution[1])
    script = tmp_path / "captions.ass"
    RenderService._write_ass(project, timeline, script, settings, "Noto Sans CJK JP")
    events = [line.rsplit(",,", 1)[-1] for line in script.read_text(encoding="utf-8").splitlines()
              if line.startswith("Dialogue: 0,")]
    assert "".join(event.replace("\\N", "") for event in events) == text
    assert all(event.count("\\N") <= 1 for event in events)


@pytest.mark.parametrize("width,height", [(960, 540), (1920, 1080)])
def test_large_caption_is_visible_within_frame(tmp_path, width, height):
    project = ProjectManager().create(tmp_path / f"project-{width}", "subtitles")
    text = "「大谷選手は勝負の瞬間を迎えた」と語った。" * 6
    caption = SimpleNamespace(id="cap", text=text, start_ms=0, end_ms=4000)
    timeline = SimpleNamespace(captions=[caption], headline={})
    settings = RenderSettings(width=width, height=height)
    font, font_path = RenderService._resolve_font(settings)
    script = tmp_path / f"captions-{width}.ass"
    RenderService._write_ass(project, timeline, script, settings, font)
    ass = script.read_text(encoding="utf-8")
    assert f"Style: Normal,{font},{round(156 * height / 1080)}," in ass
    assert ass.count("Dialogue: 0,") > 1
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    font_dir = f":fontsdir='{font_path.parent}'" if font_path else ""
    frame = tmp_path / f"caption-{width}.png"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=black:s={width}x{height}:d=0.2", "-vf",
        f"ass=filename='{script}'{font_dir}", "-frames:v", "1", str(frame)],
        capture_output=True, check=True)
    with Image.open(frame) as image:
        pixels = image.convert("RGB")
        bright = [(x, y) for y in range(height) for x in range(width)
                  if all(channel > 180 for channel in pixels.getpixel((x, y)))]
    assert bright, "Japanese caption did not render"
    xs, ys = zip(*bright)
    assert min(xs) >= width * 0.04 and max(xs) <= width * 0.96
    assert min(ys) >= 0 and max(ys) < height * 0.97
