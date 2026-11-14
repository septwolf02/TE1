"""Independent ASS box opacity with legacy rendering and project persistence."""
from copy import deepcopy
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from te.project import ProjectManager
from te.project.models import Project
from te.render import RenderService, RenderSettings
from te.render.service import _ass_filter
from te.studio.service import StudioService


def ass_script(project, tmp_path, *, width=1920, height=1080):
    project = deepcopy(project)
    project.subtitle_captions = [SimpleNamespace(id="cap", presentation="headline")]
    timeline = SimpleNamespace(captions=[SimpleNamespace(
        id="cap", text="日本語の見出し。", start_ms=0, end_ms=9000)],
        headline={"enabled": True, "duration_seconds": 7, "darkening": 0.7})
    path = tmp_path / "captions.ass"
    RenderService._write_ass(project, timeline, path,
                             RenderSettings(width=width, height=height), "Noto Sans CJK JP")
    script = path.read_text(encoding="utf-8")
    normal = next(line for line in script.splitlines() if line.startswith("Style: Normal,"))
    return script, normal.split(",")


@pytest.mark.parametrize("opacity,alpha", [(0, "FF"), (25, "BF"), (40, "99"),
                                            (60, "66"), (100, "00")])
def test_ass_alpha_is_inverse_of_box_opacity_and_text_stays_opaque(tmp_path, opacity, alpha):
    project = ProjectManager().create(tmp_path / "project", "Opacity")
    project.subtitle_style["background_opacity"] = opacity
    project.subtitle_style["headline_background_opacity"] = opacity
    for size in ((960, 540), (1920, 1080)):
        script, normal = ass_script(project, tmp_path, width=size[0], height=size[1])
        assert normal[3] == "&H00FFFFFF"  # PrimaryColour alpha remains fully opaque.
        assert normal[5] == f"&H{alpha}000000"  # BorderStyle=3 box uses OutlineColour in libass.
        assert normal[6] == f"&H{alpha}000000"
        assert normal[15] == "3"  # ASS opaque-box border style.
        assert f"\\1a&H{alpha}&\\p1" in script
        assert "{\\alpha&H00&}" in script


def test_regular_and_headline_opacity_are_independent_and_box_can_be_disabled(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "Independent")
    project.subtitle_style.update(background_opacity=25, headline_background_opacity=60)
    script, normal = ass_script(project, tmp_path)
    assert normal[5] == "&HBF000000"
    assert normal[6] == "&HBF000000"
    assert "\\1a&H66&\\p1" in script
    project.subtitle_style["background_enabled"] = False
    script, normal = ass_script(project, tmp_path)
    assert normal[6] == "&HFF000000" and normal[15] == "1"
    assert normal[3] == "&H00FFFFFF" and normal[5] == "&H00000000"
    assert "\\1a&H66&\\p1" in script  # Headline background stays enabled.


def test_new_defaults_legacy_visuals_persistence_and_duplication(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("Background")
    style = state.project.subtitle_style
    assert (style["background_enabled"], style["background_opacity"],
            style["headline_background_opacity"]) == (True, 40, 40)
    style.update(background_enabled=False, background_opacity=25, headline_background_opacity=60)
    service.save_project(state)
    reopened = service.open_project(state.directory)
    assert all(reopened.project.subtitle_style[key] == style[key] for key in (
        "background_enabled", "background_opacity", "headline_background_opacity"))
    duplicate = service.duplicate_project(reopened)
    assert duplicate.project.subtitle_style == reopened.project.subtitle_style

    legacy = deepcopy(reopened.project)
    for key in ("background_enabled", "background_opacity", "headline_background_opacity"):
        legacy.subtitle_style.pop(key)
    reopened.project = legacy
    service.save_project(reopened)
    old = service.open_project(reopened.directory)
    assert "background_opacity" not in old.project.subtitle_style
    data = old.project.to_dict()
    data.pop("subtitle_style")
    assert "background_opacity" not in Project.from_dict(data).subtitle_style
    script, normal = ass_script(old.project, tmp_path)
    assert normal[5] == "&H00000000" and normal[6] == "&H88000000" and normal[15] == "3"
    assert "\\1a&H4D&\\p1" in script  # Legacy 70% timeline darkening.


def test_invalid_opacity_is_rejected_without_accepting_bool_as_percent(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "Invalid")
    for value in (-1, 101, True, 40.5):
        project.subtitle_style["background_opacity"] = value
        with pytest.raises(ValueError, match="subtitle background"):
            project.validate()


def _synthetic_frame(project, timeline, tmp_path, name):
    settings = RenderSettings(width=640, height=360, subtitle_font_size=120)
    font, font_path = RenderService._resolve_font(settings)
    ass = tmp_path / f"{name}.ass"
    RenderService._write_ass(project, timeline, ass, settings, font)
    result = subprocess.run([RenderService.check_ffmpeg()["ffmpeg"], "-v", "error", "-f", "lavfi",
        "-i", "color=c=0xA0B4C8:s=640x360:r=1:d=1", "-vf",
        _ass_filter(ass, font_path.parent if font_path else None) + ",format=rgb24",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True)
    return Image.frombytes("RGB", (640, 360), result.stdout)


def _box_sample(image):
    """Find a wide, flat black run in the opaque ASS box, away from glyphs."""
    best = (0, None)
    for y in range(215, 350):
        start = None
        for x in range(64, 577):
            dark = max(image.getpixel((x, y))) < 8
            if dark and start is None:
                start = x
            if start is not None and (not dark or x == 576):
                end = x if dark else x - 1
                if end - start + 1 > best[0]:
                    best = (end - start + 1, ((start + end) // 2, y))
                start = None
    assert best[0] >= 30, "No solid subtitle box was found in the rendered frame"
    return best[1]


def test_ffmpeg_libass_box_pixels_follow_all_five_opacities(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "Visual opacity")
    project.subtitle_captions = [SimpleNamespace(id="cap", presentation="default")]
    timeline = SimpleNamespace(captions=[SimpleNamespace(id="cap", text="　日本語字幕　",
        start_ms=0, end_ms=1000)], headline={})
    frames = {}
    for opacity in (0, 25, 40, 60, 100):
        project.subtitle_style["background_opacity"] = opacity
        frames[opacity] = _synthetic_frame(project, timeline, tmp_path, str(opacity))
    sample = _box_sample(frames[100])
    background = frames[0].getpixel((20, 20))
    box_colors = [frames[opacity].getpixel(sample) for opacity in (0, 25, 40, 60, 100)]
    assert all(abs(channel - original) <= 5 for channel, original in zip(box_colors[0], background))
    assert max(box_colors[-1]) < 8
    assert all(all(left[channel] > right[channel] + 15 for channel in range(3))
               for left, right in zip(box_colors, box_colors[1:]))
    white = next((x, y) for y in range(215, 350) for x in range(100, 540)
                 if min(frames[100].getpixel((x, y))) >= 250)
    assert all(min(frame.getpixel(white)) >= 245 for frame in frames.values())


def test_ffmpeg_headline_background_is_independent_of_normal_box(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "Headline visual")
    project.subtitle_captions = [SimpleNamespace(id="cap", presentation="headline")]
    timeline = SimpleNamespace(captions=[SimpleNamespace(id="cap", text="日本語の見出し。",
        start_ms=0, end_ms=9000)], headline={"enabled": True, "duration_seconds": 7})
    project.subtitle_style.update(background_opacity=0, headline_background_opacity=40)
    first = _synthetic_frame(project, timeline, tmp_path, "headline-a")
    project.subtitle_style["background_opacity"] = 100
    second = _synthetic_frame(project, timeline, tmp_path, "headline-b")
    assert first.getpixel((100, 100)) == second.getpixel((100, 100))
    project.subtitle_style["headline_background_opacity"] = 0
    clear = _synthetic_frame(project, timeline, tmp_path, "headline-clear")
    project.subtitle_style["headline_background_opacity"] = 100
    dark = _synthetic_frame(project, timeline, tmp_path, "headline-dark")
    assert all(abs(a - b) <= 5 for a, b in zip(clear.getpixel((100, 100)),
                                                clear.getpixel((120, 100))))
    assert max(dark.getpixel((100, 100))) < 8
    assert all(clear.getpixel((100, 100))[index] > first.getpixel((100, 100))[index] >
               dark.getpixel((100, 100))[index] for index in range(3))
