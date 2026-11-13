"""B2 voice planning and sentence-local WAV production; the renderer sees ordinary WAVs."""
from __future__ import annotations

from array import array
from contextlib import nullcontext
from copy import copy, deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import unicodedata
from tempfile import NamedTemporaryFile
import wave

from te.project.models import SectionName, SentenceAudioRecord
from te.tts.service import _duration
from te.tts.sentences import SentenceSpeechService, _wav_rms
from te.tts.voicevox import VoicevoxProvider
from .ai_speakers import ROLES, script_fingerprint, validate_speaker_data


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def configuration_signature(state) -> str:
    return _digest((script_fingerprint(state.sections), state.speaker_data["status"],
                    state.speaker_data["assignments"], state.speaker_data["voices"],
                    state.speaker_data.get("parameters", {}), asdict(state.project.tts_settings)))


def speech_plan(state) -> tuple[dict[str, list[tuple[str, str]]], tuple[str, ...]]:
    """Intersect confirmed Japanese spans with existing sentence IDs, preserving every character."""
    data = state.speaker_data
    if data["status"] != "confirmed" or data["fingerprint"] != script_fingerprint(state.sections):
        raise ValueError("Phân vai đã cũ hoặc chưa xác nhận. Mở AI phân vai để xem lại, hoặc chọn Một giọng.")
    rows = data["assignments"]
    if any(not row.get("confirmed") or row.get("role") not in ROLES or row.get("japanese") is None
           for row in rows):
        raise ValueError("Còn đoạn phân vai chưa xác nhận hoặc chưa căn chỉnh tiếng Nhật.")
    by_section = {section.value: [] for section in SectionName}
    for row in rows:
        start, end = row["japanese"]
        source = state.sections[row["section"]]["ja"]
        if not 0 <= start < end <= len(source) or not source[start:end].strip():
            raise ValueError("Span tiếng Nhật không còn hợp lệ; hãy xác nhận lại phân vai.")
        by_section[row["section"]].append((start, end, row["role"]))
    for section, spans in by_section.items():
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError(f"Đoạn phân vai tiếng Nhật {section} bị chồng lấn.")

    result: dict[str, list[tuple[str, str]]] = {}
    fallback: set[str] = set()
    for section in SectionName:
        source = state.sections[section.value]["ja"]
        cursor = 0
        segments = [s for s in state.project.segments if s.section == section]
        for segment in segments:
            segment_start = source.find(segment.japanese_text, cursor)
            if segment_start < 0:
                raise ValueError(f"Nội dung {section.value} khác script đã lưu; hãy lưu và xác nhận lại.")
            cursor = segment_start + len(segment.japanese_text)
            local = 0
            units = sorted((u for u in state.project.sentence_units if u.segment_id == segment.id),
                           key=lambda u: u.order)
            for unit in units:
                position = segment.japanese_text.find(unit.synthesis_text, local)
                if position != local:
                    raise ValueError("Câu tiếng Nhật đã đổi; hãy lưu script trước khi tạo lời đọc.")
                start, end = segment_start + local, segment_start + local + len(unit.synthesis_text)
                local += len(unit.synthesis_text)
                boundaries = {start, end}
                for left, right, _ in by_section[section.value]:
                    if left < end and right > start:
                        boundaries.update((max(start, left), min(end, right)))
                ordered = sorted(boundaries)
                parts: list[tuple[str, str]] = []
                for left, right in zip(ordered, ordered[1:]):
                    text = source[left:right]
                    role = next((role for a, b, role in by_section[section.value] if a <= left and right <= b), "A")
                    if role == "A" and text.strip() and not any(
                            a <= left and right <= b for a, b, _ in by_section[section.value]):
                        fallback.add(section.value)
                    if parts and parts[-1][0] == role:
                        parts[-1] = (role, parts[-1][1] + text)
                    else:
                        parts.append((role, text))
                if "".join(text for _, text in parts) != unit.synthesis_text:
                    raise ValueError("Lỗi phân đoạn lời Nhật; không tạo WAV để tránh mất chữ.")
                result[unit.id] = parts
            if local != len(segment.japanese_text):
                raise ValueError("Câu tiếng Nhật chưa bao phủ toàn bộ đoạn.")
    return result, tuple(f"{section}: đoạn Nhật chưa gán vai dùng giọng A."
                         for section in sorted(fallback))


def confirmed_speaker_data(state, rows: list[dict], identities: dict[str, str]) -> dict:
    """Validate a B1 candidate with the exact B2 speech planner before replacing saved data."""
    candidate = deepcopy(state.speaker_data)
    candidate["assignments"] = deepcopy(rows)
    candidate["identities"] = dict(identities)
    for item in candidate["assignments"]:
        item["confirmed"] = True
    candidate["fingerprint"] = script_fingerprint(state.sections)
    candidate["status"] = "confirmed"
    candidate = validate_speaker_data(candidate, state.sections)
    inspected = copy(state)
    inspected.speaker_data = candidate
    _, warnings = speech_plan(inspected)
    if warnings:
        raise ValueError("Còn đoạn Nhật chưa phân vai: " + " ".join(warnings))
    return candidate


class MultiVoiceService:
    def __init__(self, provider: VoicevoxProvider | None = None):
        self.provider = provider or VoicevoxProvider(timeout=30)

    def _settings(self, state, plan):
        base = state.project.tts_settings
        if base.provider != "voicevox":
            raise ValueError("Đa giọng cần VOICEVOX ở mục 6.")
        if any(segment.tts_override is not None and asdict(segment.tts_override) != asdict(base)
               for segment in state.project.segments):
            raise ValueError("Dự án có TTS override theo đoạn; hãy dùng Một giọng hoặc bỏ override trước khi dùng đa giọng.")
        engine_version = self.provider.check_connection(base)
        catalog = self.provider.list_voices(base)
        used = {role for parts in plan.values() for role, text in parts if text.strip()}
        settings = {}
        for role in used:
            saved = state.speaker_data["voices"].get(role)
            if role != "A" and saved is None:
                raise ValueError(f"Thiếu giọng {role}. Mở AI phân vai để chọn giọng, hoặc chọn Một giọng.")
            if saved:
                speaker = next((item for item in catalog if item.get("speaker_uuid") == saved["speaker_uuid"]), None)
                style = next((item for item in speaker.get("styles", [])
                              if item.get("id") == saved["style_id"]), None) if speaker else None
            else:
                speaker = next((item for item in catalog if item.get("name") == base.voice_id or
                                item.get("speaker_uuid") == base.voice_id), None)
                style = next((item for item in speaker.get("styles", []) if item.get("name") == base.style),
                             None) if speaker and base.style else None
                if speaker and style is None:
                    style = next((item for item in speaker.get("styles", []) if item.get("name") == "ノーマル"),
                                 next(iter(speaker.get("styles", [])), None))
            if not speaker or not style:
                raise ValueError(f"Giọng hoặc style của vai {role} không có trong VOICEVOX Engine.")
            current = deepcopy(base)
            current.voice_id = speaker.get("speaker_uuid") or speaker["name"]
            current.style = style["name"]
            for name, value in state.speaker_data.get("parameters", {}).get(role, {}).items():
                setattr(current, name, value)
            settings[role] = (current, speaker.get("speaker_uuid"), style["id"])
        return settings, engine_version

    @staticmethod
    def _compose(paths: list[Path], destination: Path, sample_rate: int) -> None:
        """Use one mono PCM timeline for all speakers, including differing engine rates."""
        temporary = None
        try:
            with NamedTemporaryFile(dir=destination.parent, suffix=".wav", delete=False) as stream:
                temporary = Path(stream.name)
            with wave.open(str(temporary), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                for path in paths:
                    with wave.open(str(path), "rb") as source:
                        if source.getsampwidth() != 2:
                            raise ValueError("VOICEVOX WAV phải là PCM 16-bit.")
                        samples = array("h")
                        samples.frombytes(source.readframes(source.getnframes()))
                        if sys.byteorder != "little":
                            samples.byteswap()
                        if source.getnchannels() == 2:
                            samples = array("h", ((samples[index] + samples[index + 1]) // 2
                                                   for index in range(0, len(samples), 2)))
                        elif source.getnchannels() != 1:
                            raise ValueError("VOICEVOX WAV có số kênh không hỗ trợ.")
                        if source.getframerate() != sample_rate:
                            source_rate = source.getframerate()
                            count = round(len(samples) * sample_rate / source_rate)
                            converted = array("h")
                            for index in range(count):
                                position = index * source_rate / sample_rate
                                left = min(int(position), len(samples) - 1)
                                right = min(left + 1, len(samples) - 1)
                                value = round(samples[left] * (1 - (position - int(position))) +
                                              samples[right] * (position - int(position)))
                                converted.append(value)
                            samples = converted
                        if sys.byteorder != "little":
                            samples.byteswap()
                        output.writeframes(samples.tobytes())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def prepare(self, state, working, progress=None, performance=None) -> tuple[dict[str, str], tuple[str, ...], int]:
        plan, warnings = speech_plan(state)
        with performance.measure("voicevox_setup") if performance else nullcontext():
            settings, engine_version = self._settings(state, plan)
        chunk_dir = state.directory / "generated" / "audio" / "multi_voice"
        sentence_dir = state.directory / "generated" / "audio" / "sentences"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        sentence_dir.mkdir(parents=True, exist_ok=True)
        records = {record.sentence_id: record for record in working.sentence_audio_records}
        manifest = {}
        changed = 0
        for segment in working.segments:
            units = sorted((u for u in working.sentence_units if u.segment_id == segment.id), key=lambda u: u.order)
            segment_changed = state.audio_mode != "multi"
            for unit in units:
                chunks: list[tuple[str, str]] = []
                leading = ""
                for role, text in plan[unit.id]:
                    if not any(unicodedata.category(char)[0] in ("L", "N") for char in text):
                        if chunks:
                            old_role, old_text = chunks[-1]
                            chunks[-1] = old_role, old_text + text
                        else:
                            leading += text
                    else:
                        chunks.append((role, leading + text))
                        leading = ""
                if not chunks:
                    raise ValueError("Câu tiếng Nhật chỉ chứa khoảng trắng; hãy sửa script.")
                if "".join(text for _, text in chunks) != unit.synthesis_text:
                    raise ValueError("Lỗi bao phủ văn bản tiếng Nhật khi tạo WAV đa giọng.")
                signature = _digest([(role, text, uuid, style_id, asdict(current), engine_version)
                                     for role, text in chunks
                                     for current, uuid, style_id in [settings[role]]])
                manifest[unit.id] = signature
                previous = records.get(unit.id)
                with performance.measure("wav_cache_lookup") if performance else nullcontext():
                    sentence_hit = bool(state.audio_mode == "multi" and
                        state.multi_voice_manifest.get(unit.id) == signature and previous and
                        SentenceSpeechService._sentence_status(
                            working, unit, segment.tts_override or working.tts_settings) == "ready")
                if sentence_hit:
                    if performance:
                        performance.cache_hit(previous.path)
                    continue
                paths = []
                for role, text in chunks:
                    current, uuid, style_id = settings[role]
                    key = _digest((text, uuid, style_id, asdict(current), engine_version))
                    path = chunk_dir / f"{key}.wav"
                    usable = False
                    with performance.measure("wav_cache_lookup") if performance else nullcontext():
                        if path.is_file():
                            try:
                                usable = _duration(path) > 0 and _wav_rms(path) >= 50
                            except (OSError, ValueError, wave.Error):
                                pass
                    if not usable:
                        temporary = None
                        try:
                            with NamedTemporaryFile(dir=chunk_dir, suffix=".wav", delete=False) as stream:
                                temporary = Path(stream.name)
                            with performance.measure("narration_synthesis") if performance else nullcontext():
                                self.provider.synthesize(text, current, temporary, style_id=style_id)
                                _duration(temporary)
                                if _wav_rms(temporary) < 50:
                                    raise ValueError(f"WAV của vai {role} không có âm thanh.")
                            os.replace(temporary, path)
                            temporary = None
                            if performance:
                                performance.increment("new_wavs")
                        finally:
                            if temporary is not None:
                                temporary.unlink(missing_ok=True)
                    elif performance:
                        performance.cache_hit(path)
                    paths.append(path)
                destination = sentence_dir / f"{unit.id}.wav"
                with performance.measure("audio_composition_muxing") if performance else nullcontext():
                    self._compose(paths, destination, working.tts_settings.sample_rate)
                if _wav_rms(destination) < 50:
                    raise ValueError("WAV đa giọng sau khi ghép không có âm thanh.")
                first_role = chunks[0][0]
                _, uuid, style_id = settings[first_role]
                record = SentenceAudioRecord(unit.id, str(destination), deepcopy(segment.tts_override or working.tts_settings),
                    sha256(unit.synthesis_text.encode("utf-8")).hexdigest(), _duration(destination), "ready",
                    datetime.now(timezone.utc).isoformat(), previous.generation_version + 1 if previous else 1,
                    speaker_uuid=uuid, style_id=style_id,
                    audio_hash=sha256(destination.read_bytes()).hexdigest())
                if previous:
                    working.sentence_audio_records[working.sentence_audio_records.index(previous)] = record
                else:
                    working.sentence_audio_records.append(record)
                records[unit.id] = record
                segment_changed = True
                changed += 1
                if progress:
                    progress(f"Đã tạo WAV đa giọng {segment.section.value} câu {unit.order}.")
            if segment_changed:
                prior = next((r for r in working.audio_records if r.segment_id == segment.id), None)
                if prior:
                    working.audio_records.remove(prior)
                segment.audio_ref = None
        return manifest, warnings, changed
