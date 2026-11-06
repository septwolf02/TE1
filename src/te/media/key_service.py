"""Editor-confirmed sentence KEY placements and deterministic ordinary-image selection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import Random

from te.project.manager import ProjectManager
from te.project.models import (AssignmentType, KeyPlacement, MediaCategory, MediaType,
                               Project, new_id)
from te.timeline.models import Timeline
from te.timeline.service import TimelineService
from te.tts.sentences import SentenceSpeechService
from .service import MediaAssignmentService, MediaLibraryService


class OrdinaryImageBag:
    """Seeded shuffle bag with a boundary guard against immediate repetition."""

    def __init__(self, media_ids: list[str], seed: int):
        self.media_ids = sorted(media_ids)
        self.random = Random(seed)
        self.remaining: list[str] = []
        self.last: str | None = None

    def observe(self, media_id: str) -> None:
        self.last = media_id
        if media_id in self.remaining:
            self.remaining.remove(media_id)

    def draw(self) -> str:
        if not self.media_ids:
            raise ValueError("No ordinary images available")
        if not self.remaining:
            self.remaining = self.media_ids.copy()
            self.random.shuffle(self.remaining)
        if len(self.media_ids) > 1 and self.remaining[-1] == self.last:
            alternative = next((index for index, media_id in enumerate(self.remaining)
                                if media_id != self.last), None)
            if alternative is None:
                self.remaining = [media_id for media_id in self.media_ids if media_id != self.last]
                self.random.shuffle(self.remaining)
            else:
                self.remaining[-1], self.remaining[alternative] = self.remaining[alternative], self.remaining[-1]
        chosen = self.remaining.pop()
        self.last = chosen
        return chosen


@dataclass(frozen=True)
class KeyContext:
    sentence_id: str
    japanese_text: str
    vietnamese_segment_text: str | None
    vietnamese_sentence_text: str | None
    mapping_confirmed: bool


class KeyPlacementService:
    @staticmethod
    def context(project: Project, sentence_id: str) -> KeyContext:
        unit = next((unit for unit in project.sentence_units if unit.id == sentence_id), None)
        if unit is None:
            raise KeyError(sentence_id)
        segment = next(segment for segment in project.segments if segment.id == unit.segment_id)
        return KeyContext(unit.id, unit.synthesis_text, segment.vietnamese_reference,
                          unit.vietnamese_reference, unit.mapping_confirmed)

    @staticmethod
    def confirm_mapping(project: Project, directory: Path, sentence_id: str, vietnamese_text: str) -> KeyContext:
        SentenceSpeechService.confirm_vietnamese_mapping(project, sentence_id, vietnamese_text)
        ProjectManager().save(directory, project)
        return KeyPlacementService.context(project, sentence_id)

    @staticmethod
    def assign_key(project: Project, directory: Path, media_id: str, sentence_id: str, *,
                   offset_ms: int = 0, duration_ms: int | None = None,
                   locked: bool = True, notes: str = "") -> KeyPlacement:
        asset = MediaLibraryService._media(project, media_id)
        if asset.category != MediaCategory.KEY or asset.media_type != MediaType.IMAGE:
            raise ValueError("Only a marked KEY image can be placed")
        context = KeyPlacementService.context(project, sentence_id)
        if not context.mapping_confirmed or not context.vietnamese_sentence_text:
            raise ValueError("Confirm the VN-JP sentence mapping before placing KEY media")
        if any(item.media_id == media_id for item in project.media_assignments):
            raise ValueError("Remove the segment media assignment before placing this KEY image")
        if any(item.media_id == media_id for item in project.key_placements):
            raise ValueError("This KEY image already has a sentence placement")
        placement = KeyPlacement(new_id("plc"), media_id, sentence_id,
            context.vietnamese_sentence_text, offset_ms, duration_ms, locked, notes=notes)
        project.key_placements.append(placement)
        try:
            project.validate()
            ProjectManager().save(directory, project)
        except Exception:
            project.key_placements.pop()
            raise
        return placement

    @staticmethod
    def set_timing(project: Project, directory: Path, placement_id: str, *,
                   offset_ms: int | None = None, duration_ms: int | None = None) -> KeyPlacement:
        placement = next(item for item in project.key_placements if item.id == placement_id)
        old = (placement.offset_ms, placement.duration_ms)
        if offset_ms is not None:
            placement.offset_ms = offset_ms
        if duration_ms is not None:
            placement.duration_ms = duration_ms
        try:
            project.validate()
            ProjectManager().save(directory, project)
        except Exception:
            placement.offset_ms, placement.duration_ms = old
            raise
        return placement

    @staticmethod
    def set_locked(project: Project, directory: Path, placement_id: str, locked: bool) -> None:
        placement = next(item for item in project.key_placements if item.id == placement_id)
        placement.locked = locked
        ProjectManager().save(directory, project)

    @staticmethod
    def retarget_key(project: Project, directory: Path, placement_id: str, sentence_id: str, *,
                     force: bool = False) -> KeyPlacement:
        """Explicit editor review after a deleted sentence or changed VN-JP mapping."""
        placement = next(item for item in project.key_placements if item.id == placement_id)
        if placement.locked and not force:
            raise ValueError("Unlock the KEY placement or pass force=True to retarget")
        context = KeyPlacementService.context(project, sentence_id)
        if not context.mapping_confirmed or not context.vietnamese_sentence_text:
            raise ValueError("Confirm the new VN-JP sentence mapping first")
        old = (placement.sentence_id, placement.confirmed_vietnamese_text, placement.status)
        placement.sentence_id = sentence_id
        placement.confirmed_vietnamese_text = context.vietnamese_sentence_text
        placement.status = "ready"
        try:
            project.validate()
            ProjectManager().save(directory, project)
        except Exception:
            placement.sentence_id, placement.confirmed_vietnamese_text, placement.status = old
            raise
        return placement

    @staticmethod
    def resolve(project: Project, timeline: Timeline, placement: KeyPlacement) -> tuple[int, int]:
        if placement.status != "ready":
            raise ValueError("KEY placement needs manual review")
        unit = next((unit for unit in project.sentence_units if unit.id == placement.sentence_id), None)
        if unit is None or not unit.mapping_confirmed or unit.vietnamese_reference != placement.confirmed_vietnamese_text:
            raise ValueError("KEY placement sentence mapping is no longer confirmed")
        spoken_start, spoken_end = TimelineService.get_sentence_range(project, timeline, unit.id)
        block = TimelineService.get_segment_block(timeline, unit.segment_id)
        start = spoken_start + placement.offset_ms
        if not block.start_ms <= start < block.end_ms:
            raise ValueError("KEY offset must keep reveal inside its narration segment")
        duration = placement.duration_ms or (spoken_end - spoken_start)
        section_end = max(item.end_ms for item in timeline.blocks if item.section == block.section)
        end = min(start + duration, section_end)
        if end <= start:
            raise ValueError("KEY placement has no visible duration")
        return start, end

    @staticmethod
    def assign_random_images(project: Project, directory: Path, *, persist: bool = True) -> tuple[str, ...]:
        """Keep saved assignments; choose only normal images with a seeded reuse gap."""
        candidates = sorted((asset for asset in project.media if asset.available and
            asset.media_type == MediaType.IMAGE and asset.category == MediaCategory.NORMAL),
            key=lambda a: a.path.casefold())
        if not candidates:
            return ()
        bag = OrdinaryImageBag([asset.id for asset in candidates], project.random_seed)
        by_segment = {assignment.segment_id: assignment for assignment in project.media_assignments}
        created: list[str] = []
        assignment_service = MediaAssignmentService()
        for segment in project.segments:
            existing = by_segment.get(segment.id)
            if existing:
                bag.observe(existing.media_id)
                continue
            chosen = bag.draw()
            assignment_service.assign_media(project, segment.id, chosen,
                assignment_type=AssignmentType.RANDOM)
            created.append(segment.id)
        if persist:
            ProjectManager().save(directory, project)
        return tuple(created)
