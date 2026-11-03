"""Replaceable module ports; implementations will be added in later modules."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from te.project.models import MediaAsset, Project, ScriptSegment
from te.timeline.models import Caption, Placement, Timeline
from te.project.models import TTSSettings


class ScriptProcessor(Protocol):
    def parse(self, text: str) -> Sequence[ScriptSegment]: ...


class SpeechSynthesizer(Protocol):
    provider_id: str

    def synthesize(self, text: str, settings: TTSSettings, output_path: Path) -> None: ...


class MediaLibrary(Protocol):
    def list_assets(self, project: Project) -> Sequence[MediaAsset]: ...


class SubtitleAligner(Protocol):
    def align(self, segment: ScriptSegment, audio_path: Path) -> Sequence[Caption]: ...


class MediaSelector(Protocol):
    def select(self, project: Project) -> Sequence[Placement]: ...


class TimelineBuilder(Protocol):
    def build(self, project: Project, placements: Sequence[Placement], captions: Sequence[Caption]) -> Timeline: ...


class EffectProcessor(Protocol):
    def apply(self, timeline: Timeline) -> Timeline: ...


class Renderer(Protocol):
    def render(self, project: Project, timeline: Timeline, output_path: Path) -> Path: ...
