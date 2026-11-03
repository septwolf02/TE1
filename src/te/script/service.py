"""Strict UTF-8 script import and transactional segment editing."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re

from te.project.models import Project, ScriptSegment, SectionName, new_id

SECTION = re.compile(r'^\s*\[?(P[1-4])\]?\s*$')
NUMBERED = re.compile(r'^(\d+)([.)])([ \t])(.*)$')


@dataclass(frozen=True)
class ParsedSegment:
    section: SectionName
    number: int
    text: str


@dataclass(frozen=True)
class ImportResult:
    segment_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def parse_script(path: Path) -> list[ParsedSegment]:
    """Accept [P1]..[P4] and `1. text`/`1) text`; retain narration bytes as Unicode text."""
    with Path(path).open('r', encoding='utf-8', newline='') as stream:
        content = stream.read()
    lines = content.splitlines(keepends=True)
    result: list[ParsedSegment] = []
    seen_sections: set[SectionName] = set()
    section: SectionName | None = None
    body: list[str] = []
    number: int | None = None

    def flush() -> None:
        nonlocal body, number
        if number is not None:
            value = ''.join(body).rstrip('\r\n')
            if not value.strip():
                raise ValueError(f'Empty segment {number}')
            result.append(ParsedSegment(section, number, value))
            body = []
            number = None

    for line in lines:
        bare = line.rstrip('\r\n')
        marker = SECTION.fullmatch(bare)
        if marker:
            flush()
            next_section = SectionName(marker.group(1))
            if next_section in seen_sections or (section and list(SectionName).index(next_section) <= list(SectionName).index(section)):
                raise ValueError(f'Duplicate or out-of-order section {next_section}')
            seen_sections.add(next_section)
            section = next_section
            continue
        numbered = NUMBERED.match(bare)
        if numbered:
            if section is None:
                raise ValueError('Numbered segment before P1 marker')
            flush()
            number = int(numbered.group(1))
            if number != len(result) + 1:
                raise ValueError(f'Malformed numbering: expected {len(result)+1}, got {number}')
            body = [numbered.group(4) + line[len(bare):]]
            continue
        if bare.strip() and number is None:
            raise ValueError(f'Unnumbered content: {bare[:40]}')
        if number is not None:
            body.append(line)
    flush()
    if seen_sections != set(SectionName):
        raise ValueError(f'Missing sections: {", ".join(s.value for s in SectionName if s not in seen_sections)}')
    return result


class ScriptService:
    def import_files(self, project: Project, japanese_path: Path, vietnamese_path: Path | None = None) -> ImportResult:
        if project.segments:
            raise ValueError('Import requires an empty project; edit existing segments through this service')
        japanese = parse_script(japanese_path)
        vietnamese = parse_script(vietnamese_path) if vietnamese_path else None
        warnings: list[str] = []
        aligned = vietnamese is not None and len(vietnamese) == len(japanese) and all(
            (j.number, j.section) == (v.number, v.section) for j, v in zip(japanese, vietnamese))
        if vietnamese is not None and not aligned:
            warnings.append('Vietnamese count/order/sections do not match Japanese; no references were assigned')
        proposed = [ScriptSegment(new_id('seg'), j.section, j.number, j.text,
                    vietnamese[i].text if aligned else None) for i, j in enumerate(japanese)]
        self._commit(project, proposed)
        return ImportResult(tuple(s.id for s in proposed), tuple(warnings))

    def _commit(self, project: Project, proposed: list[ScriptSegment]) -> None:
        previous = project.segments
        project.segments = proposed
        try:
            project.validate()
        except Exception:
            project.segments = previous
            raise

    def add(self, project: Project, section: SectionName, text: str, *, at: int | None = None,
            vietnamese: str | None = None) -> ScriptSegment:
        section = SectionName(section)
        segments = deepcopy(project.segments)
        if at is None:
            at = next((i for i, s in enumerate(segments) if list(SectionName).index(s.section) > list(SectionName).index(section)), len(segments))
        segment = ScriptSegment(new_id('seg'), section, 0, text, vietnamese)
        segments.insert(at, segment)
        self._renumber(segments)
        self._commit(project, segments)
        return segment

    def delete(self, project: Project, segment_id: str) -> None:
        segments = deepcopy(project.segments)
        segment = self._find(segments, segment_id)
        if segment.audio_ref or segment.caption_refs or segment.media_placement_refs:
            raise ValueError('Segment has downstream references; clear them before deletion')
        segments.remove(segment)
        self._renumber(segments)
        self._commit(project, segments)

    def split(self, project: Project, segment_id: str, offset: int) -> tuple[ScriptSegment, ScriptSegment]:
        segments = deepcopy(project.segments)
        first = self._find(segments, segment_id)
        if not 0 < offset < len(first.japanese_text):
            raise ValueError('Split offset must be inside Japanese text')
        if first.vietnamese_reference is not None or first.audio_ref or first.caption_refs or first.media_placement_refs:
            raise ValueError('Split requires explicit handling of aligned/downstream references')
        second = ScriptSegment(new_id('seg'), first.section, 0, first.japanese_text[offset:])
        first.japanese_text = first.japanese_text[:offset]
        segments.insert(segments.index(first) + 1, second)
        self._renumber(segments)
        self._commit(project, segments)
        return first, second

    def merge(self, project: Project, first_id: str, second_id: str) -> ScriptSegment:
        segments = deepcopy(project.segments)
        first = self._find(segments, first_id)
        index = segments.index(first)
        if index + 1 >= len(segments) or segments[index + 1].id != second_id:
            raise ValueError('Only adjacent segments can be merged')
        second = segments[index + 1]
        if first.section != second.section:
            raise ValueError('Cannot merge across sections')
        if any((first.audio_ref, second.audio_ref, first.caption_refs, second.caption_refs,
                first.media_placement_refs, second.media_placement_refs)):
            raise ValueError('Merge requires explicit handling of downstream references')
        first.japanese_text += second.japanese_text
        if first.vietnamese_reference is not None and second.vietnamese_reference is not None:
            first.vietnamese_reference += second.vietnamese_reference
        elif first.vietnamese_reference is not None or second.vietnamese_reference is not None:
            raise ValueError('Cannot merge partially aligned Vietnamese references')
        first.notes = '\n'.join(x for x in (first.notes, second.notes) if x)
        del segments[index + 1]
        self._renumber(segments)
        self._commit(project, segments)
        return first

    def reorder(self, project: Project, ordered_ids: list[str]) -> None:
        if len(ordered_ids) != len(project.segments) or set(ordered_ids) != {s.id for s in project.segments}:
            raise ValueError('Reorder requires every segment ID exactly once')
        by_id = {s.id: s for s in deepcopy(project.segments)}
        segments = [by_id[id] for id in ordered_ids]
        self._renumber(segments)
        self._commit(project, segments)

    def move(self, project: Project, segment_id: str, section: SectionName, *, at: int | None = None) -> None:
        segments = deepcopy(project.segments)
        segment = self._find(segments, segment_id)
        segments.remove(segment)
        section = SectionName(section)
        segment.section = section
        if at is None:
            at = next((i for i, s in enumerate(segments) if list(SectionName).index(s.section) > list(SectionName).index(section)), len(segments))
        segments.insert(at, segment)
        self._renumber(segments)
        self._commit(project, segments)

    def edit(self, project: Project, segment_id: str, *, japanese: str | None = None,
             vietnamese: str | None = None, clear_vietnamese: bool = False,
             notes: str | None = None, status: str | None = None) -> None:
        segments = deepcopy(project.segments)
        segment = self._find(segments, segment_id)
        if japanese is not None:
            segment.japanese_text = japanese
        if clear_vietnamese:
            segment.vietnamese_reference = None
        elif vietnamese is not None:
            segment.vietnamese_reference = vietnamese
        if notes is not None:
            segment.notes = notes
        if status is not None:
            segment.status = status
        self._commit(project, segments)

    @staticmethod
    def _find(segments: list[ScriptSegment], segment_id: str) -> ScriptSegment:
        try:
            return next(s for s in segments if s.id == segment_id)
        except StopIteration:
            raise KeyError(segment_id) from None

    @staticmethod
    def _renumber(segments: list[ScriptSegment]) -> None:
        for index, segment in enumerate(segments, 1):
            segment.number = index

    @staticmethod
    def warnings(project: Project) -> list[str]:
        count = sum(s.vietnamese_reference is not None for s in project.segments)
        return ['Japanese/Vietnamese count mismatch'] if 0 < count < len(project.segments) else []
