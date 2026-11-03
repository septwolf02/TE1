"""Segment-local caption splitting, alignment and manual correction."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from te.project.manager import ProjectManager
from te.project.models import Project, SubtitleCaption, new_id
from te.tts import TTSService


def split_japanese_sentences(text: str) -> list[str]:
    """Split at 。！？ and retain closing quotes and all original characters."""
    closers = "」』）】"
    result: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] in "。！？":
            end = index + 1
            while end < len(text) and text[end] in closers:
                end += 1
            if text[start:end].strip():
                result.append(text[start:end])
            start = end
            index = end
        else:
            index += 1
    if start < len(text):
        if result and not text[start:].strip():
            result[-1] += text[start:]
        else:
            result.append(text[start:])
    return result


def wrap_caption(text: str, max_chars_per_line: int = 24, max_lines: int = 2) -> list[str]:
    """View-only wrapping; returns lines and never changes the stored caption text."""
    if max_chars_per_line < 1 or max_lines < 1:
        raise ValueError("Invalid wrapping limits")
    lines = [text[i:i + max_chars_per_line] for i in range(0, len(text), max_chars_per_line)]
    if len(lines) > max_lines:
        raise ValueError("Caption exceeds display limits; edit or split it")
    return lines


class AlignmentProvider(Protocol):
    method: str
    status: str
    confidence: float

    def align(self, texts: list[str], duration_ms: int) -> list[tuple[int, int]]: ...


class DeterministicAligner:
    method = "deterministic_test"
    status = "aligned"
    confidence = 1.0

    def align(self, texts: list[str], duration_ms: int) -> list[tuple[int, int]]:
        return _allocate([1] * len(texts), duration_ms)


class ProportionalAligner:
    method = "proportional"
    status = "approximate"
    confidence = 0.35

    def align(self, texts: list[str], duration_ms: int) -> list[tuple[int, int]]:
        return _allocate([max(1, len(text)) for text in texts], duration_ms)


def _allocate(weights: list[int], duration_ms: int) -> list[tuple[int, int]]:
    if duration_ms < len(weights):
        raise ValueError("Audio is too short for one millisecond per caption")
    total = sum(weights)
    boundaries = [0]
    for index, weight in enumerate(weights[:-1], 1):
        target = round(sum(weights[:index]) * duration_ms / total)
        boundaries.append(max(boundaries[-1] + 1, min(target, duration_ms - (len(weights) - index))))
    boundaries.append(duration_ms)
    return list(zip(boundaries, boundaries[1:]))


class SubtitleService:
    def __init__(self, providers: dict[str, AlignmentProvider] | None = None):
        self.providers = providers or {
            "deterministic_test": DeterministicAligner(), "proportional": ProportionalAligner(),
        }
        self.tts = TTSService()

    @staticmethod
    def _segment(project: Project, segment_id: str):
        return next((s for s in project.segments if s.id == segment_id), None)

    @staticmethod
    def _captions(project: Project, segment_id: str) -> list[SubtitleCaption]:
        return [c for c in project.subtitle_captions if c.segment_id == segment_id]

    @staticmethod
    def _hash(texts: list[str]) -> str:
        return sha256("\0".join(texts).encode("utf-8")).hexdigest()

    @staticmethod
    def _source_hash(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _audio_fingerprint(project: Project, segment_id: str) -> str | None:
        record = next((r for r in project.audio_records if r.segment_id == segment_id), None)
        if record is None or not record.path or not Path(record.path).is_file():
            return None
        digest = sha256()
        with Path(record.path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(str(record.duration_seconds).encode())
        digest.update(str(record.generation_version).encode())
        return digest.hexdigest()

    def split_segment_into_captions(self, project: Project, segment_id: str) -> list[SubtitleCaption]:
        segment = self._segment(project, segment_id)
        if segment is None:
            raise KeyError(segment_id)
        if self._captions(project, segment_id):
            raise ValueError("Captions already exist; edit or explicitly regenerate them")
        captions = [SubtitleCaption(new_id("cap"), segment_id, segment.section, index, text,
                                    presentation="headline" if segment.section == "P1" and segment.number == 1 and index <= 2 else "default",
                                    source_text_hash=self._source_hash(segment.japanese_text))
                    for index, text in enumerate(split_japanese_sentences(segment.japanese_text), 1)]
        project.subtitle_captions.extend(captions)
        segment.caption_refs.extend(c.id for c in captions)
        project.validate()
        return captions

    def generate_all_captions(self, project: Project) -> list[SubtitleCaption]:
        return [caption for segment in project.segments if not self._captions(project, segment.id)
                for caption in self.split_segment_into_captions(project, segment.id)]

    def regenerate_captions(self, project: Project, segment_id: str, *, overwrite_locked: bool = False) -> list[SubtitleCaption]:
        """Re-split changed narration; preserve IDs for unchanged sentence positions."""
        segment = self._segment(project, segment_id)
        if segment is None:
            raise KeyError(segment_id)
        old = self._captions(project, segment_id)
        if any(c.locked for c in old) and not overwrite_locked:
            raise ValueError("Locked captions require explicit overwrite")
        texts = split_japanese_sentences(segment.japanese_text)
        replacement = []
        for index, text in enumerate(texts, 1):
            keep_id = old[index - 1].id if index <= len(old) and old[index - 1].text == text else new_id("cap")
            replacement.append(SubtitleCaption(
                keep_id, segment_id, segment.section, index, text,
                presentation="headline" if segment.section == "P1" and segment.number == 1 and index <= 2 else "default",
                source_text_hash=self._source_hash(segment.japanese_text),
            ))
        project.subtitle_captions = [c for c in project.subtitle_captions if c.segment_id != segment_id] + replacement
        segment.caption_refs = [c.id for c in replacement]
        project.validate()
        return replacement

    def detect_stale_alignment(self, project: Project, segment_id: str) -> bool:
        captions = self._captions(project, segment_id)
        if not captions:
            return True
        if self.tts.get_audio_status(project, segment_id) != "ready":
            return True
        fingerprint = self._audio_fingerprint(project, segment_id)
        split_hash = self._hash([c.text for c in captions])
        source_hash = self._source_hash(self._segment(project, segment_id).japanese_text)
        return any(c.alignment_status in ("unaligned", "stale") or
                   c.audio_fingerprint != fingerprint or c.split_hash != split_hash or
                   c.source_text_hash != source_hash for c in captions)

    def align_segment(self, project: Project, directory: Path, segment_id: str, *,
                      provider_id: str = "proportional", overwrite_locked: bool = False) -> str:
        if self._segment(project, segment_id) is None:
            raise KeyError(segment_id)
        audio_status = self.tts.get_audio_status(project, segment_id)
        if audio_status != "ready":
            return f"audio_{audio_status}"
        captions = self._captions(project, segment_id)
        if not captions:
            captions = self.split_segment_into_captions(project, segment_id)
        source_hash = self._source_hash(self._segment(project, segment_id).japanese_text)
        if any(c.source_text_hash != source_hash for c in captions):
            return "stale_split"
        if any(c.locked for c in captions) and not overwrite_locked:
            return "locked"
        provider = self.providers[provider_id]
        record = next(r for r in project.audio_records if r.segment_id == segment_id)
        duration_ms = round(record.duration_seconds * 1000)
        windows = provider.align([c.text for c in captions], duration_ms)
        if len(windows) != len(captions):
            raise ValueError("Aligner returned wrong caption count")
        fingerprint = self._audio_fingerprint(project, segment_id)
        split_hash = self._hash([c.text for c in captions])
        before = deepcopy(captions)
        for caption, (start, end) in zip(captions, windows):
            caption.start_ms, caption.end_ms = start, end
            caption.alignment_method = provider.method
            caption.alignment_status = provider.status
            caption.confidence = provider.confidence
            caption.audio_fingerprint = fingerprint
            caption.split_hash = split_hash
        try:
            self.validate_captions(project, segment_id, require_contiguous=True)
            ProjectManager().save(directory, project)
        except Exception:
            for old, caption in zip(before, captions):
                for name in old.__dataclass_fields__:
                    setattr(caption, name, getattr(old, name))
            raise
        return provider.status

    def align_selected(self, project: Project, directory: Path, segment_ids: list[str], **kwargs) -> dict[str, str]:
        return {segment_id: self.align_segment(project, directory, segment_id, **kwargs) for segment_id in segment_ids}

    def align_all(self, project: Project, directory: Path, **kwargs) -> dict[str, str]:
        return self.align_selected(project, directory, [s.id for s in project.segments if self.detect_stale_alignment(project, s.id)], **kwargs)

    def edit_caption(self, project: Project, caption_id: str, text: str) -> None:
        caption = self._caption(project, caption_id)
        old = caption.text
        caption.text = text
        try:
            project.validate()
        except Exception:
            caption.text = old
            raise

    def split_caption(self, project: Project, caption_id: str, offset: int) -> tuple[SubtitleCaption, SubtitleCaption]:
        first = self._caption(project, caption_id)
        if first.locked or not 0 < offset < len(first.text):
            raise ValueError("Cannot split locked caption or invalid offset")
        captions = self._captions(project, first.segment_id)
        second = SubtitleCaption(new_id("cap"), first.segment_id, first.section, first.order + 1, first.text[offset:],
                                 source_text_hash=first.source_text_hash)
        first.text = first.text[:offset]
        first.start_ms = first.end_ms = None
        first.alignment_status = "stale"
        second.alignment_status = "stale"
        project.subtitle_captions.insert(project.subtitle_captions.index(first) + 1, second)
        for caption in captions:
            if caption.order > first.order:
                caption.order += 1
        segment = self._segment(project, first.segment_id)
        segment.caption_refs.insert(segment.caption_refs.index(first.id) + 1, second.id)
        project.validate()
        return first, second

    def merge_captions(self, project: Project, first_id: str, second_id: str) -> SubtitleCaption:
        first, second = self._caption(project, first_id), self._caption(project, second_id)
        if first.segment_id != second.segment_id or second.order != first.order + 1 or first.locked or second.locked:
            raise ValueError("Only adjacent unlocked captions in one segment can merge")
        first.text += second.text
        first.start_ms = first.end_ms = None
        first.alignment_status = "stale"
        project.subtitle_captions.remove(second)
        segment = self._segment(project, first.segment_id)
        segment.caption_refs.remove(second.id)
        for caption in self._captions(project, first.segment_id):
            if caption.order > second.order:
                caption.order -= 1
        project.validate()
        return first

    def adjust_timing(self, project: Project, caption_id: str, start_ms: int, end_ms: int) -> None:
        caption = self._caption(project, caption_id)
        previous = (caption.start_ms, caption.end_ms, caption.alignment_status, caption.alignment_method)
        caption.start_ms, caption.end_ms = start_ms, end_ms
        caption.alignment_status, caption.alignment_method = "manual", "manual"
        try:
            self.validate_captions(project, caption.segment_id)
        except Exception:
            caption.start_ms, caption.end_ms, caption.alignment_status, caption.alignment_method = previous
            raise

    def lock_timing(self, project: Project, caption_id: str, locked: bool = True) -> None:
        caption = self._caption(project, caption_id)
        caption.locked = locked
        project.validate()

    def validate_captions(self, project: Project, segment_id: str, *, require_contiguous: bool = False) -> None:
        project.validate()
        captions = self._captions(project, segment_id)
        record = next((r for r in project.audio_records if r.segment_id == segment_id), None)
        limit = round(record.duration_seconds * 1000) if record and record.duration_seconds else None
        previous_end = None
        for caption in captions:
            if caption.start_ms is None:
                continue
            if limit is not None and caption.end_ms > limit:
                raise ValueError("Caption exceeds parent audio duration")
            if previous_end is not None and caption.start_ms < previous_end:
                raise ValueError("Captions overlap")
            if require_contiguous and previous_end is not None and caption.start_ms != previous_end:
                raise ValueError("Gap between captions")
            previous_end = caption.end_ms
        if captions and any(c.start_ms is not None for c in captions) and self.tts.get_audio_status(project, segment_id) != "ready":
            raise ValueError("Alignment uses stale or missing audio")
        if any(c.start_ms is not None and c.audio_fingerprint != self._audio_fingerprint(project, segment_id)
               for c in captions):
            raise ValueError("Alignment uses changed audio")

    @staticmethod
    def _caption(project: Project, caption_id: str) -> SubtitleCaption:
        try:
            return next(c for c in project.subtitle_captions if c.id == caption_id)
        except StopIteration:
            raise KeyError(caption_id) from None
