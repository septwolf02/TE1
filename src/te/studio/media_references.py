"""Media dependencies used by a Studio render, excluding historical catalog entries."""
from __future__ import annotations

from pathlib import Path

from te.project.models import AssignmentType
from .service import StudioProject


def active_media_references(state: StudioProject) -> dict[Path, list[str]]:
    project = state.project
    media = {item.id: item for item in project.media}
    segments = {item.id: item for item in project.segments}
    units = {item.id: item for item in project.sentence_units}
    references: dict[Path, list[str]] = {}

    def add(media_id: str, label: str) -> None:
        asset = media.get(media_id)
        if asset:
            references.setdefault(Path(asset.path).resolve(), []).append(label)

    used_sections = {segment.section.value for segment in project.segments}
    for section, media_id in project.section_intros.items():
        if media_id and section in used_sections and state.intro_preferences.get(section, True):
            add(media_id, f"video Intro {section}")
    key_counts: dict[str, int] = {}
    for placement in project.key_placements:
        unit = units.get(placement.sentence_id)
        segment = segments.get(unit.segment_id) if unit else None
        section = segment.section.value if segment else "không rõ phần"
        key_counts[section] = key_counts.get(section, 0) + 1
        add(placement.media_id, f"media KEY {section}, mục số {key_counts[section]}")
    for assignment in project.media_assignments:
        if assignment.assignment_type == AssignmentType.RANDOM and not assignment.locked:
            continue  # The timeline builder may replace a missing random image.
        segment = segments.get(assignment.segment_id)
        if segment:
            add(assignment.media_id, f"ảnh thường {segment.section.value}, đoạn {segment.number}")
    return references


def missing_media(state: StudioProject, *, origin: str = "project hiện tại") -> list[str]:
    return [f"Thiếu {', '.join(labels)} ({origin}): {path}. Hãy chọn lại media hoặc cập nhật tác vụ từ project."
            for path, labels in active_media_references(state).items() if not path.is_file()]
