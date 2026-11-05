"""Catch production packages accidentally excluded from the release checkout."""

from te.media import KeyPlacementService, MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager
from te.render import RenderService
from te.script import ScriptService
from te.subtitles import SubtitleService
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService
from te.studio import StudioService


def test_module_services_importable():
    services = (
        ProjectManager, ScriptService, TTSService, SentenceSpeechService, MediaLibraryService,
        MediaAssignmentService, KeyPlacementService, SubtitleService, TimelineService, RenderService,
        StudioService,
    )
    assert all(callable(service) for service in services)
