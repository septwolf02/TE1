"""Deterministic, audio-led storytelling timeline construction."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from dataclasses import replace

from te.media import MediaAssignmentService
from te.project.manager import _write_json
from te.project.models import MediaType, Project, SectionName, new_id
from te.subtitles import SubtitleService
from te.tts import TTSService
from .models import (Caption, EFFECTS, IntroEvent, Placement, SectionMusic, SegmentBlock,
                     Timeline, TransitionEvent, TRANSITIONS, VIDEO_POLICIES)


class TimelineService:
    def __init__(self):
        self.tts = TTSService()
        self.subtitles = SubtitleService()

    @staticmethod
    def _fingerprint(project: Project) -> str:
        payload = project.to_dict()
        return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def build_timeline(self, project: Project, directory: Path, *, previous: Timeline | None = None,
                       effect_sequence: tuple[str, ...] = ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right"),
                       video_policy: str = "trim_to_segment", intro_max_ms: int | None = None,
                       intro_overlap: bool = False,
                       intro_enabled: dict[str, bool] | None = None) -> tuple[Timeline, tuple[str, ...]]:
        project.validate()
        if not effect_sequence or any(effect not in EFFECTS for effect in effect_sequence):
            raise ValueError("Invalid effect sequence")
        if video_policy not in VIDEO_POLICIES:
            raise ValueError("Invalid video policy")
        if intro_max_ms is not None and intro_max_ms <= 0:
            raise ValueError("Invalid intro trim duration")
        previous = previous or Timeline(project.id)
        timeline = Timeline(project.id)
        timeline.music = list(previous.music)
        timeline.headline = dict(previous.headline)
        saved_effects = {b.segment_id: b for b in previous.blocks}
        saved_transitions = {t.to_section: t.preset for t in previous.transitions}
        warnings: list[str] = []
        cursor = 0
        last_section = None
        visual_index = 0
        media = {item.id: item for item in project.media}
        assignments = {item.segment_id: item for item in project.media_assignments}
        for segment in project.segments:
            audio_status = self.tts.get_audio_status(project, segment.id)
            if audio_status != "ready":
                raise ValueError(f"Segment {segment.id} has {audio_status} TTS audio")
            record = next(item for item in project.audio_records if item.segment_id == segment.id)
            if last_section != segment.section:
                if last_section is not None:
                    timeline.transitions.append(TransitionEvent(last_section, segment.section, cursor,
                        saved_transitions.get(segment.section, "cut")))
                intro_id = (project.section_intros.get(segment.section.value)
                            if intro_enabled is None or intro_enabled.get(segment.section.value, True) else None)
                if intro_id:
                    intro = media[intro_id]
                    if intro.available and intro.duration_seconds is not None and intro.duration_seconds > 0:
                        length = round(intro.duration_seconds * 1000)
                        policy = "keep_original"
                        if intro_max_ms and length > intro_max_ms:
                            length, policy = intro_max_ms, "trim"
                        if intro_overlap:
                            section_length = sum(round(item.duration_seconds * 1000)
                                for member in project.segments if member.section == segment.section
                                for item in project.audio_records if item.segment_id == member.id
                                and item.duration_seconds is not None)
                            if length > section_length:
                                length, policy = section_length, "trim"
                        if length > 0:
                            timeline.intros.append(IntroEvent(segment.section, intro.id, cursor, cursor + length, policy))
                            if not intro_overlap:
                                cursor += length
                    else:
                        warnings.append(f"Intro for {segment.section} is missing or has unknown duration")
                last_section = segment.section
            captions = self.subtitles._captions(project, segment.id)
            if not captions:
                raise ValueError(f"Segment {segment.id} has missing subtitles")
            if captions and self.subtitles.detect_stale_alignment(project, segment.id):
                raise ValueError(f"Segment {segment.id} has stale subtitles")
            length = round(record.duration_seconds * 1000)
            start = cursor
            end = start + length
            assignment = assignments.get(segment.id)
            asset = media.get(assignment.media_id) if assignment else None
            visual_status = "ready" if asset and asset.available else "missing"
            if visual_status == "missing":
                warnings.append(f"Missing visual for segment {segment.id}")
            if asset and asset.media_type == MediaType.VIDEO and video_policy == "keep_original":
                if asset.duration_seconds is None or round(asset.duration_seconds * 1000) != length:
                    raise ValueError("keep_original requires video duration equal to narration segment")
            effect = "none"
            if asset and asset.media_type == MediaType.IMAGE:
                saved = saved_effects.get(segment.id)
                effect = assignment.effect_preference or (saved.effect if saved and saved.effect_locked else effect_sequence[visual_index % len(effect_sequence)])
                visual_index += 1
            if effect not in EFFECTS:
                raise ValueError(f"Invalid effect: {effect}")
            if asset and asset.category == "key" and not MediaAssignmentService().is_media_allowed_at(project, asset.id, segment.id):
                raise ValueError("KEY visual appears before reveal segment")
            saved = saved_effects.get(segment.id)
            block = SegmentBlock(segment.id, segment.section, start, end, record.path,
                asset.id if asset else None, visual_status, [c.id for c in captions], effect,
                bool(saved and saved.effect_locked), bool(assignment and assignment.locked),
                video_policy if asset and asset.media_type == MediaType.VIDEO else "trim_to_segment")
            timeline.blocks.append(block)
            if asset and asset.available:
                timeline.placements.append(Placement(assignment.id, asset.id, segment.section,
                    start, end, segment.id, [{"name": effect}] if effect != "none" else []))
            for caption in captions:
                if caption.start_ms is None or caption.end_ms is None:
                    raise ValueError(f"Unaligned caption {caption.id}")
                timeline.captions.append(Caption(caption.id, segment.id, caption.text,
                    start + caption.start_ms, start + caption.end_ms))
            cursor = end
        from te.media.key_service import KeyPlacementService
        intervals: list[tuple[int, int]] = []
        for key in project.key_placements:
            if key.status != "ready":
                warnings.append(f"KEY placement {key.id} needs manual review")
                continue
            asset = media[key.media_id]
            if not asset.available:
                warnings.append(f"KEY image unavailable: {asset.path}")
                continue
            start, end = KeyPlacementService.resolve(project, timeline, key)
            if any(start < old_end and old_start < end for old_start, old_end in intervals):
                raise ValueError("KEY image placements overlap")
            intervals.append((start, end))
            unit = next(unit for unit in project.sentence_units if unit.id == key.sentence_id)
            if any(start < intro.end_ms and intro.start_ms < end for intro in timeline.intros):
                raise ValueError(f"KEY {key.id} trùng thời gian intro; hãy đổi intro hoặc thời điểm KEY.")
            for base in list(timeline.placements):
                if base.end_ms <= start or base.start_ms >= end:
                    continue
                timeline.placements.remove(base)
                if base.start_ms < start:
                    timeline.placements.append(replace(base, end_ms=start))
                if end < base.end_ms:
                    timeline.placements.append(replace(base,
                        id=new_id("plc") if base.start_ms < start else base.id, start_ms=end))
            segment = next(segment for segment in project.segments if segment.id == unit.segment_id)
            timeline.placements.append(Placement(key.id, key.media_id, segment.section,
                start, end, segment.id))
        timeline.placements.sort(key=lambda placement: (placement.start_ms, placement.end_ms))
        timeline.source_fingerprint = self._fingerprint(project)
        timeline.validate(project)
        _write_json(Path(directory) / project.timeline_file, timeline.to_dict())
        return timeline, tuple(warnings)

    def rebuild_timeline(self, project: Project, directory: Path, **kwargs):
        previous = self.load_timeline(project, directory)
        return self.build_timeline(project, directory, previous=previous, **kwargs)

    @staticmethod
    def save_timeline(project: Project, directory: Path, timeline: Timeline) -> None:
        timeline.validate(project)
        _write_json(Path(directory) / project.timeline_file, timeline.to_dict())

    @staticmethod
    def load_timeline(project: Project, directory: Path) -> Timeline:
        return Timeline.from_dict(json.loads((Path(directory) / project.timeline_file).read_text(encoding="utf-8")), project)

    def detect_stale_timeline(self, project: Project, timeline: Timeline) -> bool:
        return timeline.source_fingerprint != self._fingerprint(project)

    def validate_timeline(self, project: Project, timeline: Timeline) -> tuple[str, ...]:
        timeline.validate(project)
        if self.detect_stale_timeline(project, timeline):
            raise ValueError("Stale timeline")
        from te.media.key_service import KeyPlacementService
        for key in project.key_placements:
            actual = [placement for placement in timeline.placements if placement.media_id == key.media_id]
            if key.status != "ready" or not next(m for m in project.media if m.id == key.media_id).available:
                if actual:
                    raise ValueError("Unavailable or unreviewed KEY image appears in timeline")
                continue
            expected = KeyPlacementService.resolve(project, timeline, key)
            if len(actual) != 1 or (actual[0].start_ms, actual[0].end_ms) != expected:
                raise ValueError("KEY image reveal differs from confirmed sentence timing")
        warnings = [f"Missing visual for segment {b.segment_id}" for b in timeline.blocks if b.visual_status == "missing"]
        warnings.extend(f"KEY placement {key.id} needs manual review"
                        for key in project.key_placements if key.status == "manual_review")
        return tuple(warnings)

    @staticmethod
    def get_segment_block(timeline: Timeline, segment_id: str) -> SegmentBlock:
        return next(block for block in timeline.blocks if block.segment_id == segment_id)

    @staticmethod
    def get_sentence_range(project: Project, timeline: Timeline, sentence_id: str) -> tuple[int, int]:
        """Absolute spoken interval in milliseconds for a stable sentence ID."""
        unit = next((unit for unit in project.sentence_units if unit.id == sentence_id), None)
        if unit is None:
            raise KeyError(sentence_id)
        captions = [caption for caption in project.subtitle_captions if caption.sentence_id == sentence_id]
        if not captions:
            raise ValueError(f"Sentence {sentence_id} has no aligned caption")
        caption_ids = {caption.id for caption in captions}
        placed = [item for item in timeline.captions if item.id in caption_ids]
        if len(placed) != len(captions):
            raise ValueError(f"Sentence {sentence_id} is missing from timeline")
        return min(item.start_ms for item in placed), max(item.end_ms for item in placed)

    @staticmethod
    def get_section_range(timeline: Timeline, section: SectionName) -> tuple[int, int] | None:
        starts = [b.start_ms for b in timeline.blocks if b.section == section]
        ends = [b.end_ms for b in timeline.blocks if b.section == section]
        intros = [i for i in timeline.intros if i.section == section]
        if intros:
            starts.append(intros[0].start_ms)
            ends.append(intros[-1].end_ms)
        return (min(starts), max(ends)) if starts else None

    @staticmethod
    def set_effect(timeline: Timeline, segment_id: str, effect: str) -> None:
        if effect not in EFFECTS:
            raise ValueError("Invalid effect")
        block = TimelineService.get_segment_block(timeline, segment_id)
        if block.effect_locked:
            raise ValueError("Effect is locked")
        block.effect = effect

    @staticmethod
    def lock_effect(timeline: Timeline, segment_id: str, locked: bool = True) -> None:
        TimelineService.get_segment_block(timeline, segment_id).effect_locked = locked

    @staticmethod
    def set_transition(timeline: Timeline, to_section: SectionName, preset: str) -> None:
        if preset not in TRANSITIONS:
            raise ValueError("Invalid transition")
        next(event for event in timeline.transitions if event.to_section == to_section).preset = preset

    @staticmethod
    def assign_section_music(timeline: Timeline, section: SectionName, path: Path, *,
                             volume: float = 0.2, fade_in_ms: int = 0, fade_out_ms: int = 0) -> None:
        path = Path(path).resolve()
        timeline.music = [item for item in timeline.music if item.section != section]
        timeline.music.append(SectionMusic(SectionName(section), str(path), volume, fade_in_ms, fade_out_ms))
        timeline.validate()

    @staticmethod
    def set_headline_config(timeline: Timeline, *, darkening: float = 0.7, enabled: bool = True,
                            duration_seconds: float | None = None) -> None:
        timeline.headline.update({"darkening": darkening, "enabled": enabled})
        if duration_seconds is not None:
            timeline.headline["duration_seconds"] = duration_seconds
        timeline.validate()
