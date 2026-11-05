"""Segment-addressed WAV generation; providers can be replaced independently."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
import wave

from te.project.models import AudioRecord, Project, ScriptSegment, TTSSettings
from te.project.manager import ProjectManager


class TTSProvider(Protocol):
    provider_id: str

    def synthesize(self, text: str, settings: TTSSettings, output_path: Path) -> dict | None:
        """Write one valid WAV. Input is the exact stored narration unless explicitly transformed."""


class FakeTTSProvider:
    """Deterministic silence: one second per ten characters, minimum one second."""
    provider_id = "fake"

    def synthesize(self, text: str, settings: TTSSettings, output_path: Path) -> None:
        frames = max(1, round(max(1.0, len(text) / 10) * settings.sample_rate / settings.speaking_rate))
        with wave.open(str(output_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(settings.sample_rate)
            stream.writeframes(b"\x00\x00" * frames)


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getnframes() <= 0 or stream.getframerate() <= 0:
            raise ValueError("Empty or invalid WAV")
        return stream.getnframes() / stream.getframerate()


class TTSService:
    def __init__(self, providers: dict[str, TTSProvider] | None = None):
        if providers is None:
            from .voicevox import VoicevoxProvider
            providers = {"fake": FakeTTSProvider(), "voicevox": VoicevoxProvider()}
        self.providers = providers

    @staticmethod
    def _settings(project: Project, segment: ScriptSegment) -> TTSSettings:
        return segment.tts_override or project.tts_settings

    @staticmethod
    def _segment(project: Project, segment_id: str) -> ScriptSegment:
        try:
            return next(s for s in project.segments if s.id == segment_id)
        except StopIteration:
            raise KeyError(segment_id) from None

    @staticmethod
    def _record(project: Project, segment_id: str) -> AudioRecord | None:
        return next((r for r in project.audio_records if r.segment_id == segment_id), None)

    @staticmethod
    def _hash(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    def detect_stale_audio(self, project: Project, segment_id: str) -> bool:
        segment = self._segment(project, segment_id)
        record = self._record(project, segment_id)
        if record is None:
            return False
        current = self._settings(project, segment)
        return (record.text_hash != self._hash(segment.japanese_text)
                or record.provider_id != current.provider
                or asdict(record.settings) != asdict(current))

    def get_audio_status(self, project: Project, segment_id: str) -> str:
        record = self._record(project, segment_id)
        if record is None:
            return "missing"
        if self.detect_stale_audio(project, segment_id):
            return "stale"
        if record.status == "failed":
            return "failed"
        if not record.path or not Path(record.path).is_file():
            return "missing_file"
        try:
            if _duration(Path(record.path)) != record.duration_seconds:
                return "invalid_file"
        except (OSError, ValueError, wave.Error):
            return "invalid_file"
        return "ready"

    def validate_audio(self, project: Project) -> dict[str, str]:
        return {s.id: self.get_audio_status(project, s.id) for s in project.segments}

    def generate_segment(self, project: Project, directory: Path, segment_id: str,
                         *, settings: TTSSettings | None = None) -> AudioRecord:
        segment = self._segment(project, segment_id)
        settings = replace(settings or self._settings(project, segment))
        provider = self.providers.get(settings.provider)
        if provider is None:
            raise ValueError(f"Unknown TTS provider: {settings.provider}")
        if settings is not None:
            Project._validate_tts_settings(settings)
        project.validate()
        project_dir = Path(directory).resolve()
        if ProjectManager().open(project_dir).id != project.id:
            raise ValueError("Project directory does not match project ID")
        output_dir = project_dir / "generated" / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{segment.id}.wav"
        previous = self._record(project, segment_id)
        version = (previous.generation_version + 1) if previous else 1
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=output_dir, suffix=".wav", delete=False) as stream:
                temporary = Path(stream.name)
            # No preprocessing: the provider sees exactly the stored Japanese text.
            metadata = provider.synthesize(segment.japanese_text, settings, temporary) or {}
            duration = _duration(temporary)
            os.replace(temporary, output_path)
            temporary = None
            record = AudioRecord(segment_id, str(output_path), provider.provider_id,
                settings.voice_id, settings, duration, "ready",
                datetime.now(timezone.utc).isoformat(), version,
                segment.japanese_text, segment.japanese_text, self._hash(segment.japanese_text),
                speaker_uuid=metadata.get("speaker_uuid"), style_id=metadata.get("style_id"),
                credit=metadata.get("credit"), license_reference=metadata.get("license_reference"),
                license_text=metadata.get("license_text"))
        except Exception as exc:
            record = AudioRecord(segment_id, previous.path if previous else None,
                provider.provider_id, settings.voice_id, settings,
                previous.duration_seconds if previous else None, "failed",
                datetime.now(timezone.utc).isoformat(), version,
                segment.japanese_text, segment.japanese_text, self._hash(segment.japanese_text),
                f"{type(exc).__name__}: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if previous:
            project.audio_records[project.audio_records.index(previous)] = record
        else:
            project.audio_records.append(record)
        if settings != self._settings(project, segment):
            segment.tts_override = replace(settings)
        segment.audio_ref = record.path
        project.validate()
        ProjectManager().save(project_dir, project)
        return record

    def save_voice_preset(self, project: Project, directory: Path, name: str,
                          settings: TTSSettings) -> None:
        Project._validate_tts_settings(settings)
        if not name.strip():
            raise ValueError("Voice preset name cannot be empty")
        project.voice_presets[name] = replace(settings)
        ProjectManager().save(directory, project)

    def apply_voice_preset(self, project: Project, directory: Path, name: str,
                           segment_id: str | None = None) -> None:
        settings = replace(project.voice_presets[name])
        if segment_id is None:
            project.tts_settings = settings
        else:
            self._segment(project, segment_id).tts_override = settings
        ProjectManager().save(directory, project)

    def regenerate_segment(self, project: Project, directory: Path, segment_id: str,
                           *, settings: TTSSettings | None = None) -> AudioRecord:
        return self.generate_segment(project, directory, segment_id, settings=settings)

    def regenerate_selected(self, project: Project, directory: Path, segment_ids: list[str],
                            *, continue_on_error: bool = True) -> list[AudioRecord]:
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("Duplicate segment IDs")
        for segment_id in segment_ids:
            self._segment(project, segment_id)
        records = []
        for segment_id in segment_ids:
            record = self.generate_segment(project, directory, segment_id)
            records.append(record)
            if record.status == "failed" and not continue_on_error:
                break
        return records

    def generate_all(self, project: Project, directory: Path, *,
                     continue_on_error: bool = True) -> list[AudioRecord]:
        missing = [s.id for s in project.segments if self.get_audio_status(project, s.id) != "ready"]
        return self.regenerate_selected(project, directory, missing, continue_on_error=continue_on_error)
