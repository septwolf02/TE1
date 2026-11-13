"""Sentence WAV production and measured timing anchors for existing segment renderer."""
from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import nullcontext
from array import array
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import os
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
import wave

from te.project.manager import ProjectManager
from te.project.models import AudioRecord, Project, SentenceAudioRecord, SentenceUnit, SubtitleCaption, new_id
from te.subtitles.service import _allocate, split_caption_text, split_japanese_sentences, SubtitleService
from .service import TTSService, _duration


def _wav_rms(path: Path) -> float:
    """Measure 16-bit PCM speech energy; VOICEVOX WAVs must contain audible samples."""
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Expected 16-bit narration WAV")
        samples = array("h", source.readframes(source.getnframes()))
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) if samples else 0.0


class SentenceSpeechService:
    def __init__(self, tts: TTSService | None = None):
        self.tts = tts or TTSService()

    @staticmethod
    def _hash(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    def split_segment(self, project: Project, segment_id: str) -> list[SentenceUnit]:
        segment = self.tts._segment(project, segment_id)
        texts = split_japanese_sentences(segment.japanese_text)
        old = [unit for unit in project.sentence_units if unit.segment_id == segment_id]
        replacement: list[SentenceUnit | None] = [None] * len(texts)
        rewritten_captions: dict[str, str] = {}
        matcher = SequenceMatcher(None, [unit.synthesis_text for unit in old], texts, autojunk=False)
        for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
            if tag == "equal":
                for before, after in zip(range(before_start, before_end), range(after_start, after_end)):
                    replacement[after] = old[before]
            elif tag == "replace" and before_end - before_start == after_end - after_start:
                for before, after in zip(range(before_start, before_end), range(after_start, after_end)):
                    matching = sorted((caption for caption in project.subtitle_captions
                        if caption.sentence_id == old[before].id), key=lambda caption: caption.order)
                    if matching and "".join(caption.text for caption in matching) == old[before].synthesis_text:
                        rewritten_captions[old[before].id] = texts[after]
                    replacement[after] = replace(old[before], synthesis_text=texts[after],
                                                 vietnamese_reference=None, mapping_confirmed=False)
        for index, text in enumerate(texts):
            if replacement[index] is None:
                replacement[index] = SentenceUnit(new_id("sen"), segment_id, index + 1, text)
            else:
                replacement[index].order = index + 1
        project.sentence_units = [unit for unit in project.sentence_units if unit.segment_id != segment_id] + replacement
        active = {unit.id for unit in project.sentence_units}
        project.sentence_audio_records = [record for record in project.sentence_audio_records
                                          if record.sentence_id in active]
        for placement in project.key_placements:
            unit = next((unit for unit in project.sentence_units if unit.id == placement.sentence_id), None)
            if unit is None or not unit.mapping_confirmed or unit.vietnamese_reference != placement.confirmed_vietnamese_text:
                placement.status = "manual_review"
        surviving = {unit.id: unit.order for unit in replacement}
        for sentence_id, new_text in rewritten_captions.items():
            matching = sorted((caption for caption in project.subtitle_captions
                if caption.sentence_id == sentence_id), key=lambda caption: caption.order)
            matching[0].text = new_text
            project.subtitle_captions = [caption for caption in project.subtitle_captions
                if caption not in matching[1:]]
        captions = [c for c in project.subtitle_captions if c.segment_id == segment_id]
        if any(c.sentence_id and c.sentence_id not in surviving for c in captions):
            project.subtitle_captions = [c for c in project.subtitle_captions
                if c.segment_id != segment_id or c.sentence_id is None or c.sentence_id in surviving]
        remaining = sorted((c for c in project.subtitle_captions if c.segment_id == segment_id),
                           key=lambda caption: caption.order)
        for index, caption in enumerate(remaining, 1):
            caption.order = index
        segment.caption_refs = [caption.id for caption in remaining]
        project.validate()
        return replacement

    @staticmethod
    def confirm_vietnamese_mapping(project: Project, sentence_id: str, vietnamese_text: str) -> None:
        if not vietnamese_text.strip():
            raise ValueError("Confirmed Vietnamese reference cannot be empty")
        unit = next((unit for unit in project.sentence_units if unit.id == sentence_id), None)
        if unit is None:
            raise KeyError(sentence_id)
        unit.vietnamese_reference = vietnamese_text
        unit.mapping_confirmed = True
        for placement in project.key_placements:
            if placement.sentence_id == sentence_id and placement.confirmed_vietnamese_text != vietnamese_text:
                placement.status = "manual_review"
        project.validate()

    @staticmethod
    def _sentence_status(project: Project, unit: SentenceUnit, settings) -> str:
        record = next((record for record in project.sentence_audio_records if record.sentence_id == unit.id), None)
        if record is None:
            return "missing"
        if record.text_hash != SentenceSpeechService._hash(unit.synthesis_text) or asdict(record.settings) != asdict(settings):
            return "stale"
        if record.status != "ready":
            return "failed"
        if not record.path or not Path(record.path).is_file():
            return "missing_file"
        try:
            if abs(_duration(Path(record.path)) - record.duration_seconds) > 1e-6:
                return "invalid_file"
            if record.audio_hash and sha256(Path(record.path).read_bytes()).hexdigest() != record.audio_hash:
                return "invalid_file"
            if settings.provider == "voicevox" and _wav_rms(Path(record.path)) < 50:
                return "silent"
        except (OSError, ValueError, wave.Error):
            return "invalid_file"
        return "ready"

    def get_sentence_audio_status(self, project: Project, sentence_id: str) -> str:
        unit = next((unit for unit in project.sentence_units if unit.id == sentence_id), None)
        if unit is None:
            raise KeyError(sentence_id)
        segment = self.tts._segment(project, unit.segment_id)
        return self._sentence_status(project, unit, segment.tts_override or project.tts_settings)

    def detect_stale_sentence_audio(self, project: Project, sentence_id: str) -> bool:
        return self.get_sentence_audio_status(project, sentence_id) == "stale"

    def _generate_sentence(self, project: Project, directory: Path, unit: SentenceUnit, settings) -> SentenceAudioRecord:
        provider = self.tts.providers.get(settings.provider)
        if provider is None:
            raise ValueError(f"Unknown TTS provider: {settings.provider}")
        output_dir = directory / "generated" / "audio" / "sentences"
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{unit.id}.wav"
        previous = next((r for r in project.sentence_audio_records if r.sentence_id == unit.id), None)
        version = previous.generation_version + 1 if previous else 1
        temporary = None
        try:
            with NamedTemporaryFile(dir=output_dir, suffix=".wav", delete=False) as stream:
                temporary = Path(stream.name)
            metadata = provider.synthesize(unit.synthesis_text, settings, temporary) or {}
            duration = _duration(temporary)
            if settings.provider == "voicevox" and _wav_rms(temporary) < 50:
                raise ValueError("WAV lời đọc vừa tạo không có âm thanh")
            os.replace(temporary, destination)
            temporary = None
            record = SentenceAudioRecord(unit.id, str(destination), replace(settings),
                self._hash(unit.synthesis_text), duration, "ready", datetime.now(timezone.utc).isoformat(), version,
                speaker_uuid=metadata.get("speaker_uuid"), style_id=metadata.get("style_id"),
                credit=metadata.get("credit"), license_reference=metadata.get("license_reference"),
                license_text=metadata.get("license_text"), audio_hash=sha256(destination.read_bytes()).hexdigest())
        except Exception as exc:
            record = SentenceAudioRecord(unit.id, previous.path if previous else None, replace(settings),
                self._hash(unit.synthesis_text), previous.duration_seconds if previous else None,
                "failed", datetime.now(timezone.utc).isoformat(), version,
                error=f"{type(exc).__name__}: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if previous:
            project.sentence_audio_records[project.sentence_audio_records.index(previous)] = record
        else:
            project.sentence_audio_records.append(record)
        return record

    @staticmethod
    def _combine(project: Project, units: list[SentenceUnit], destination: Path) -> list[tuple[int, int]]:
        records = {record.sentence_id: record for record in project.sentence_audio_records}
        temporary = None
        anchors: list[tuple[int, int]] = []
        try:
            with NamedTemporaryFile(dir=destination.parent, suffix=".wav", delete=False) as file:
                temporary = Path(file.name)
            with wave.open(str(temporary), "wb") as output:
                cursor = 0
                fmt = None
                for index, unit in enumerate(units):
                    with wave.open(records[unit.id].path, "rb") as source:
                        current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                        if fmt is None:
                            fmt = current
                            output.setnchannels(current[0]); output.setsampwidth(current[1]); output.setframerate(current[2])
                        elif current != fmt:
                            raise ValueError("Sentence WAV formats differ; regenerate with one sample rate")
                        start = round(cursor * 1000 / fmt[2])
                        frames = source.getnframes()
                        output.writeframes(source.readframes(frames))
                        cursor += frames
                        end = round(cursor * 1000 / fmt[2])
                        anchors.append((start, end))
                        if index < len(units) - 1:
                            pause_frames = round(project.sentence_pause_ms * fmt[2] / 1000)
                            output.writeframes(b"\0" * pause_frames * fmt[0] * fmt[1])
                            cursor += pause_frames
            os.replace(temporary, destination)
            temporary = None
            return anchors
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def sync_segment(self, project: Project, directory: Path, segment_id: str, *,
                     rebuild_timeline: bool = True, caption_max_chars: int | None = None,
                     performance=None) -> list[tuple[SentenceUnit, int, int]]:
        directory = Path(directory).resolve()
        if ProjectManager().open(directory).id != project.id:
            raise ValueError("Project directory does not match project")
        segment = self.tts._segment(project, segment_id)
        settings = replace(segment.tts_override or project.tts_settings)
        units = self.split_segment(project, segment_id)
        changed = False
        for unit in units:
            with performance.measure("wav_cache_lookup") if performance else nullcontext():
                ready = self._sentence_status(project, unit, settings) == "ready"
            if ready:
                if performance:
                    record = next(r for r in project.sentence_audio_records if r.sentence_id == unit.id)
                    performance.cache_hit(record.path)
            else:
                with performance.measure("narration_synthesis") if performance else nullcontext():
                    record = self._generate_sentence(project, directory, unit, settings)
                changed = True
                if record.status != "ready":
                    if performance:
                        performance.stages["narration_synthesis"]["status"] = "failed"
                    ProjectManager().save(directory, project)
                    raise RuntimeError(f"Sentence {unit.id} TTS failed: {record.error}")
                if performance:
                    performance.increment("new_wavs")
        composite = self.tts._record(project, segment_id)
        anchors = self._anchors(project, units)
        if composite and composite.duration_seconds is not None and round(composite.duration_seconds * 1000) != anchors[-1][1]:
            changed = True
        with performance.measure("wav_cache_lookup") if performance else nullcontext():
            composite_ready = bool(composite and self.tts.get_audio_status(project, segment_id) == "ready")
        if changed or not composite_ready:
            destination = directory / "generated" / "audio" / f"{segment.id}.wav"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with performance.measure("audio_composition_muxing") if performance else nullcontext():
                anchors = self._combine(project, units, destination)
            first = next(record for record in project.sentence_audio_records if record.sentence_id == units[0].id)
            record = AudioRecord(segment.id, str(destination), settings.provider, settings.voice_id, settings,
                _duration(destination), "ready", datetime.now(timezone.utc).isoformat(),
                composite.generation_version + 1 if composite else 1, segment.japanese_text,
                segment.japanese_text, self._hash(segment.japanese_text),
                speaker_uuid=first.speaker_uuid, style_id=first.style_id, credit=first.credit,
                license_reference=first.license_reference, license_text=first.license_text)
            if composite:
                project.audio_records[project.audio_records.index(composite)] = record
            else:
                project.audio_records.append(record)
            segment.audio_ref = record.path
        with performance.measure("subtitle_preparation") if performance else nullcontext():
            self._sync_captions(project, segment_id, units, anchors, caption_max_chars)
        ProjectManager().save(directory, project)
        if rebuild_timeline:
            self.rebuild_if_ready(project, directory)
        return [(unit, *anchor) for unit, anchor in zip(units, anchors)]

    @staticmethod
    def _anchors(project: Project, units: list[SentenceUnit]) -> list[tuple[int, int]]:
        records = {record.sentence_id: record for record in project.sentence_audio_records}
        first = records[units[0].id]
        with wave.open(first.path, "rb") as source:
            rate = source.getframerate()
        cursor = 0
        result = []
        for index, unit in enumerate(units):
            with wave.open(records[unit.id].path, "rb") as source:
                frames = source.getnframes()
            start = round(cursor * 1000 / rate)
            cursor += frames
            result.append((start, round(cursor * 1000 / rate)))
            if index < len(units) - 1:
                cursor += round(project.sentence_pause_ms * rate / 1000)
        return result

    @staticmethod
    def _sync_captions(project: Project, segment_id: str, units: list[SentenceUnit],
                       anchors: list[tuple[int, int]], caption_max_chars: int | None = None) -> None:
        segment = next(s for s in project.segments if s.id == segment_id)
        old = [c for c in project.subtitle_captions if c.segment_id == segment_id]
        by_sentence: dict[str, list[SubtitleCaption]] = {}
        current_sentence = None
        for caption in sorted(old, key=lambda item: item.order):
            if caption.sentence_id:
                current_sentence = caption.sentence_id
            if current_sentence:
                by_sentence.setdefault(current_sentence, []).append(caption)
        for group in by_sentence.values():
            group.sort(key=lambda caption: caption.order)
        fallback = old if len(old) == len(units) and all(c.sentence_id is None for c in old) else []
        fingerprint = SubtitleService._audio_fingerprint(project, segment_id)
        source_hash = SubtitleService._source_hash(segment.japanese_text)
        captions = []
        max_chars = min(project.subtitle_style["max_chars_per_line"], caption_max_chars or 1000000)
        max_lines = project.subtitle_style["max_lines"]
        for sentence_index, (unit, (start, end)) in enumerate(zip(units, anchors), 1):
            previous_parts = by_sentence.get(unit.id) or ([fallback[sentence_index - 1]] if fallback else [])
            text = "".join(part.text for part in previous_parts) if previous_parts else unit.synthesis_text
            capacity = max_chars * max_lines
            parts = ([part.text for part in previous_parts]
                     if previous_parts and all(part.text.strip() and len(part.text) <= capacity for part in previous_parts)
                     else split_caption_text(text, max_chars, max_lines))
            windows = _allocate([max(1, len(part)) for part in parts], end - start)
            for part_index, (part, (local_start, local_end)) in enumerate(zip(parts, windows)):
                previous = previous_parts[part_index] if part_index < len(previous_parts) else None
                caption = SubtitleCaption(previous.id if previous else new_id("cap"), segment_id, segment.section,
                    len(captions) + 1, part, start + local_start, start + local_end,
                    alignment_method="sentence_audio", alignment_status="aligned", confidence=1.0,
                    locked=previous.locked if previous else False,
                    notes=previous.notes if previous else "",
                    presentation=previous.presentation if previous else
                        "headline" if segment.section == "P1" and segment.number == 1 and sentence_index <= 2 else "default",
                    audio_fingerprint=fingerprint, source_text_hash=source_hash, sentence_id=unit.id)
                captions.append(caption)
        split_hash = SubtitleService._hash([c.text for c in captions])
        for caption in captions:
            caption.split_hash = split_hash
        project.subtitle_captions = [c for c in project.subtitle_captions if c.segment_id != segment_id] + captions
        segment.caption_refs = [c.id for c in captions]
        project.validate()

    @staticmethod
    def rebuild_if_ready(project: Project, directory: Path) -> bool:
        from te.timeline.service import TimelineService
        service = TimelineService()
        if any(service.tts.get_audio_status(project, s.id) != "ready" or
               service.subtitles.detect_stale_alignment(project, s.id) for s in project.segments):
            return False
        previous = service.load_timeline(project, directory)
        service.build_timeline(project, directory, previous=previous)
        return True

    def sync_all(self, project: Project, directory: Path) -> dict[str, list[tuple[SentenceUnit, int, int]]]:
        result = {segment.id: self.sync_segment(project, directory, segment.id, rebuild_timeline=False)
                  for segment in project.segments}
        self.rebuild_if_ready(project, directory)
        return result
