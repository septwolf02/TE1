"""The opening effect changes presentation without changing caption or audio timing."""
from types import SimpleNamespace

import pytest

from te.project import ProjectManager
from te.render import RenderService, RenderSettings
from te.studio.service import StudioService
from te.timeline.models import Timeline
from te.timeline.service import TimelineService


def _events(path):
    return [line.split(",", 9) for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")]


def test_headline_crossing_seven_seconds_keeps_text_and_timeline(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "headline")
    text = "日本語の見出し。"
    project.subtitle_captions = [SimpleNamespace(id="cap-1", presentation="headline")]
    caption = SimpleNamespace(id="cap-1", text=text, start_ms=3000, end_ms=11000)
    timeline = SimpleNamespace(captions=[caption], headline={"enabled": True, "darkening": 0.7})
    before = (caption.start_ms, caption.end_ms, caption.text)
    path = tmp_path / "captions.ass"
    RenderService._write_ass(project, timeline, path, RenderSettings(), "Noto Sans CJK JP")
    events = _events(path)
    overlays = [event for event in events if event[0] == "Dialogue: -1"]
    headline = [event for event in events if event[3] == "Headline" and event[0] == "Dialogue: 0"]
    normal = [event for event in events if event[3] == "Normal"]
    assert [(event[1], event[2]) for event in overlays] == [("0:00:03.00", "0:00:07.00")]
    assert [(event[1], event[2]) for event in headline] == [("0:00:03.00", "0:00:07.00")]
    assert [(event[1], event[2]) for event in normal] == [("0:00:07.00", "0:00:11.00")]
    assert all(text in event[-1] for event in headline + normal)
    assert (caption.start_ms, caption.end_ms, caption.text) == before


def test_headline_ends_at_seven_even_when_later_caption_is_marked_headline(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "headline")
    project.subtitle_captions = [SimpleNamespace(id="one", presentation="headline"),
                                 SimpleNamespace(id="two", presentation="headline")]
    timeline = SimpleNamespace(captions=[
        SimpleNamespace(id="one", text="始まり。", start_ms=0, end_ms=5000),
        SimpleNamespace(id="two", text="続き。", start_ms=7000, end_ms=10000)],
        headline={"enabled": True, "darkening": 0.7})
    path = tmp_path / "captions.ass"
    RenderService._write_ass(project, timeline, path, RenderSettings(), "Noto Sans CJK JP")
    events = _events(path)
    assert not any(event[0] == "Dialogue: -1" and event[1] >= "0:00:07.00" for event in events)
    assert any(event[3] == "Normal" and "続き。" in event[-1] for event in events)


def test_project_timeline_accepts_five_to_ten_second_headline(tmp_path):
    project = ProjectManager().create(tmp_path / "project", "headline")
    timeline = Timeline(project.id)
    TimelineService.set_headline_config(timeline, duration_seconds=9)
    TimelineService.set_headline_config(timeline, darkening=0.5)
    restored = Timeline.from_dict(timeline.to_dict())
    assert restored.headline["duration_seconds"] == 9 and restored.headline["darkening"] == 0.5
    with pytest.raises(ValueError, match="between 5 and 10"):
        TimelineService.set_headline_config(timeline, duration_seconds=11)
    studio = StudioService(tmp_path / "settings.json")
    studio.set_directories(tmp_path / "projects", tmp_path / "output")
    state = studio.create_project("Headline duration")
    state.headline_duration_seconds = 9
    studio.save_project(state)
    assert studio.open_project(state.directory).headline_duration_seconds == 9
