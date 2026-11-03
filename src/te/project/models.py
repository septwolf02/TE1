"""Versioned project model; IDs are opaque and stable across edits and refreshes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

PROJECT_VERSION = 5
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def valid_id(value: str, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix + "_"):
        return False
    try:
        UUID(hex=value[len(prefix) + 1 :])
        return len(value) == len(prefix) + 33
    except ValueError:
        return False


class SectionName(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    KEY_IMAGE = "key_image"
    SECTION_INTRO_VIDEO = "section_intro_video"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class MediaCategory(StrEnum):
    NORMAL = "normal"
    KEY = "key"
    SECTION_INTRO = "section_intro"


class AssignmentType(StrEnum):
    MANUAL = "manual"
    KEY = "key"
    SECTION_INTRO = "section_intro"
    FUTURE_AI = "future_ai"


@dataclass(slots=True)
class Section:
    id: SectionName
    title: str = ""


@dataclass(slots=True)
class ScriptSegment:
    id: str
    section: SectionName
    number: int
    japanese_text: str
    vietnamese_reference: str | None = None
    notes: str = ""
    status: str = "draft"
    audio_ref: str | None = None
    caption_refs: list[str] = field(default_factory=list)
    media_placement_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MediaAsset:
    id: str
    kind: MediaKind
    path: str
    section: SectionName | None = None
    media_type: MediaType = MediaType.IMAGE
    category: MediaCategory = MediaCategory.NORMAL
    filename: str = ""
    extension: str = ""
    file_size: int = 0
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    notes: str = ""
    available: bool = True


@dataclass(slots=True)
class MediaAssignment:
    id: str
    segment_id: str
    media_id: str
    assignment_type: AssignmentType
    locked: bool = False
    effect_preference: str | None = None
    notes: str = ""


@dataclass(slots=True)
class TTSSettings:
    provider: str = "fake"
    voice_id: str = "test"
    speaking_rate: float = 1.0
    style: str | None = None
    output_format: str = "wav"
    sample_rate: int = 16000


@dataclass(slots=True)
class AudioRecord:
    segment_id: str
    path: str | None
    provider_id: str
    voice_id: str
    settings: TTSSettings
    duration_seconds: float | None
    status: str
    generated_at: str | None
    generation_version: int
    original_text: str
    provider_input_text: str
    text_hash: str
    error: str | None = None


@dataclass(slots=True)
class SubtitleCaption:
    id: str
    segment_id: str
    section: SectionName
    order: int
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    absolute_start_ms: int | None = None
    absolute_end_ms: int | None = None
    alignment_method: str | None = None
    alignment_status: str = "unaligned"
    confidence: float | None = None
    notes: str = ""
    locked: bool = False
    presentation: str = "default"
    audio_fingerprint: str | None = None
    split_hash: str | None = None
    source_text_hash: str | None = None


@dataclass(slots=True)
class Project:
    id: str
    name: str
    sections: list[Section] = field(default_factory=lambda: [Section(s) for s in SectionName])
    segments: list[ScriptSegment] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    media_library_path: str | None = None
    media_assignments: list[MediaAssignment] = field(default_factory=list)
    section_intros: dict[str, str | None] = field(
        default_factory=lambda: {section.value: None for section in SectionName}
    )
    media_reuse_distance: int = 3
    tts_settings: TTSSettings = field(default_factory=TTSSettings)
    audio_records: list[AudioRecord] = field(default_factory=list)
    subtitle_captions: list[SubtitleCaption] = field(default_factory=list)
    subtitle_style: dict = field(default_factory=lambda: {
        "canvas_width": 1920, "canvas_height": 1080, "position": "bottom_center",
        "font": "bold_japanese", "text_color": "#FFFFFF", "outline_color": "#000000",
        "background": "semi_transparent_dark", "max_lines": 2, "max_chars_per_line": 24,
        "headline_position": "center", "headline_background": "darkened",
    })
    schema_version: int = PROJECT_VERSION
    timeline_file: str = "timeline.json"

    def validate(self) -> None:
        if self.schema_version != PROJECT_VERSION or not valid_id(self.id, "prj"):
            raise ValueError("Unsupported project version or invalid project ID")
        if not self.name.strip() or self.timeline_file != "timeline.json":
            raise ValueError("Invalid project name or timeline file")
        if [s.id for s in self.sections] != list(SectionName):
            raise ValueError("Sections must be P1, P2, P3, P4 in order")
        segment_ids: set[str] = set()
        numbers: set[int] = set()
        previous_section = 0
        for segment in self.segments:
            if not valid_id(segment.id, "seg") or segment.id in segment_ids:
                raise ValueError("Invalid or duplicate segment ID")
            if segment.number < 1 or segment.number in numbers or not segment.japanese_text.strip():
                raise ValueError("Invalid segment number or Japanese text")
            if segment.section not in SectionName:
                raise ValueError("Invalid segment section")
            if segment.number != len(numbers) + 1:
                raise ValueError("Missing or unordered segment order index")
            section_index = list(SectionName).index(segment.section)
            if section_index < previous_section:
                raise ValueError("Section ordering problem")
            previous_section = section_index
            if not isinstance(segment.notes, str) or not isinstance(segment.status, str) or not segment.status:
                raise ValueError("Invalid segment notes or status")
            if any(not valid_id(ref, "cap") for ref in segment.caption_refs) or any(
                not valid_id(ref, "plc") for ref in segment.media_placement_refs
            ):
                raise ValueError("Invalid future reference ID")
            segment_ids.add(segment.id)
            numbers.add(segment.number)
        media_ids: set[str] = set()
        for asset in self.media:
            if not valid_id(asset.id, "med") or asset.id in media_ids:
                raise ValueError("Invalid or duplicate media ID")
            if not Path(asset.path).is_absolute():
                raise ValueError("Media paths must be absolute local paths")
            if asset.filename != Path(asset.path).name or asset.extension != Path(asset.path).suffix.lower():
                raise ValueError("Media filename or extension does not match its path")
            if asset.file_size < 0 or asset.width is not None and asset.width <= 0 or asset.height is not None and asset.height <= 0:
                raise ValueError("Invalid media metadata")
            if asset.duration_seconds is not None and asset.duration_seconds < 0:
                raise ValueError("Invalid media duration")
            if not isinstance(asset.notes, str) or not isinstance(asset.available, bool):
                raise ValueError("Invalid media notes or availability")
            if asset.kind == MediaKind.SECTION_INTRO_VIDEO and asset.section is None:
                raise ValueError("Intro video requires a section")
            if asset.kind != MediaKind.SECTION_INTRO_VIDEO and asset.section is not None:
                raise ValueError("Only intro video can have a section")
            expected = {
                MediaKind.IMAGE: (MediaType.IMAGE, MediaCategory.NORMAL),
                MediaKind.VIDEO: (MediaType.VIDEO, MediaCategory.NORMAL),
                MediaKind.KEY_IMAGE: (MediaType.IMAGE, MediaCategory.KEY),
                MediaKind.SECTION_INTRO_VIDEO: (MediaType.VIDEO, MediaCategory.SECTION_INTRO),
            }[asset.kind]
            if (asset.media_type, asset.category) != expected:
                raise ValueError("Media kind, type and category disagree")
            supported = SUPPORTED_IMAGE_EXTENSIONS if asset.media_type == MediaType.IMAGE else SUPPORTED_VIDEO_EXTENSIONS
            if asset.extension not in supported:
                raise ValueError("Unsupported media type")
            media_ids.add(asset.id)
        if self.media_library_path is not None and not Path(self.media_library_path).is_absolute():
            raise ValueError("Media library path must be absolute")
        if self.media_reuse_distance < 0:
            raise ValueError("Media reuse distance cannot be negative")
        assignment_ids: set[str] = set()
        assigned_segments: set[str] = set()
        media_by_id = {asset.id: asset for asset in self.media}
        segment_by_id = {segment.id: segment for segment in self.segments}
        for assignment in self.media_assignments:
            if not valid_id(assignment.id, "plc") or assignment.id in assignment_ids:
                raise ValueError("Invalid or duplicate media assignment ID")
            if assignment.segment_id not in segment_by_id:
                raise ValueError("Assignment points to nonexistent segment")
            if assignment.media_id not in media_by_id:
                raise ValueError("Assignment points to missing media")
            if assignment.segment_id in assigned_segments:
                raise ValueError("A segment can have only one primary visual assignment")
            asset = media_by_id[assignment.media_id]
            if assignment.assignment_type == AssignmentType.KEY and asset.category != MediaCategory.KEY:
                raise ValueError("KEY assignment requires KEY media")
            if asset.category == MediaCategory.KEY and assignment.assignment_type not in (
                AssignmentType.KEY, AssignmentType.FUTURE_AI
            ):
                raise ValueError("KEY media requires a KEY or future AI assignment")
            if assignment.assignment_type == AssignmentType.SECTION_INTRO:
                if asset.category != MediaCategory.SECTION_INTRO or asset.section != segment_by_id[assignment.segment_id].section:
                    raise ValueError("Section intro assignment does not match segment section")
            if asset.category == MediaCategory.SECTION_INTRO and assignment.assignment_type != AssignmentType.SECTION_INTRO:
                raise ValueError("Section intro media requires a section intro assignment")
            if not isinstance(assignment.locked, bool) or not isinstance(assignment.notes, str):
                raise ValueError("Invalid media assignment metadata")
            if assignment.effect_preference is not None and not isinstance(assignment.effect_preference, str):
                raise ValueError("Invalid effect preference")
            if assignment.id not in segment_by_id[assignment.segment_id].media_placement_refs:
                raise ValueError("Segment is missing its media assignment reference")
            assignment_ids.add(assignment.id)
            assigned_segments.add(assignment.segment_id)
        if set(self.section_intros) != {section.value for section in SectionName}:
            raise ValueError("Section intro mapping must contain P1-P4")
        for section, media_id in self.section_intros.items():
            if media_id is None:
                continue
            asset = media_by_id.get(media_id)
            if asset is None or asset.category != MediaCategory.SECTION_INTRO or asset.section != SectionName(section):
                raise ValueError("Invalid section intro reference")
        settings = self.tts_settings
        if (not settings.provider or not settings.voice_id or settings.speaking_rate <= 0 or
                settings.output_format != "wav" or settings.sample_rate <= 0):
            raise ValueError("Invalid TTS settings")
        seen_audio: set[str] = set()
        for record in self.audio_records:
            if record.segment_id not in segment_by_id or record.segment_id in seen_audio:
                raise ValueError("Invalid or duplicate audio segment ID")
            if record.status not in ("ready", "failed") or record.generation_version < 1:
                raise ValueError("Invalid audio status or version")
            if not record.provider_id or not record.voice_id or len(record.text_hash) != 64:
                raise ValueError("Invalid audio metadata")
            if record.path is not None and not Path(record.path).is_absolute():
                raise ValueError("Audio path must be absolute")
            if record.status == "ready" and (not record.path or record.duration_seconds is None or record.duration_seconds <= 0):
                raise ValueError("Ready audio requires path and duration")
            if record.status == "failed" and not record.error:
                raise ValueError("Failed audio requires error")
            if segment_by_id[record.segment_id].audio_ref != record.path:
                raise ValueError("Segment audio reference does not match record")
            seen_audio.add(record.segment_id)
        caption_ids: set[str] = set()
        order_by_segment: dict[str, int] = {}
        for caption in self.subtitle_captions:
            if not valid_id(caption.id, "cap") or caption.id in caption_ids:
                raise ValueError("Invalid or duplicate caption ID")
            parent = segment_by_id.get(caption.segment_id)
            if parent is None or caption.section != parent.section:
                raise ValueError("Missing or mismatched caption parent segment")
            expected = order_by_segment.get(caption.segment_id, 0) + 1
            if caption.order != expected or not caption.text.strip():
                raise ValueError("Invalid caption order or empty text")
            if (caption.start_ms is None) != (caption.end_ms is None):
                raise ValueError("Incomplete caption timing")
            if caption.start_ms is not None and (caption.start_ms < 0 or caption.end_ms <= caption.start_ms):
                raise ValueError("Invalid caption timing")
            if caption.confidence is not None and not 0 <= caption.confidence <= 1:
                raise ValueError("Invalid caption confidence")
            if caption.alignment_status not in ("unaligned", "aligned", "approximate", "manual", "stale"):
                raise ValueError("Invalid caption status")
            if caption.presentation not in ("default", "headline") or not isinstance(caption.locked, bool):
                raise ValueError("Invalid caption presentation or lock")
            caption_ids.add(caption.id)
            order_by_segment[caption.segment_id] = caption.order
        if self.subtitle_style.get("max_lines") != 2 or self.subtitle_style.get("max_chars_per_line", 0) < 1:
            raise ValueError("Invalid subtitle style")

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        try:
            version = data["schema_version"]
            if version not in (1, 2, 3, 4, PROJECT_VERSION):
                raise ValueError("Unsupported project version")
            project = cls(
                id=data["id"], name=data["name"], schema_version=PROJECT_VERSION,
                timeline_file=data["timeline_file"],
                sections=[Section(SectionName(s["id"]), s["title"]) for s in data["sections"]],
                segments=[ScriptSegment(id=s["id"], section=SectionName(s["section"]),
                    number=s["number"], japanese_text=s["japanese_text"],
                    vietnamese_reference=s.get("vietnamese_reference"), notes=s.get("notes", ""),
                    status=s.get("status", "draft"), audio_ref=s.get("audio_ref"),
                    caption_refs=s.get("caption_refs", []),
                    media_placement_refs=s.get("media_placement_refs", [])) for s in data["segments"]],
                media=[cls._media_from_dict(m) for m in data["media"]],
                media_library_path=data.get("media_library_path"),
                media_assignments=[MediaAssignment(
                    id=a["id"], segment_id=a["segment_id"], media_id=a["media_id"],
                    assignment_type=AssignmentType(a["assignment_type"]), locked=a.get("locked", False),
                    effect_preference=a.get("effect_preference"), notes=a.get("notes", ""),
                ) for a in data.get("media_assignments", [])],
                section_intros=data.get("section_intros", {section.value: None for section in SectionName}),
                media_reuse_distance=data.get("media_reuse_distance", 3),
                tts_settings=TTSSettings(**data.get("tts_settings", {})),
                audio_records=[AudioRecord(
                    **{**a, "settings": TTSSettings(**a["settings"])}
                ) for a in data.get("audio_records", [])],
                subtitle_captions=[SubtitleCaption(**{
                    **c, "section": SectionName(c["section"])
                }) for c in data.get("subtitle_captions", [])],
                subtitle_style=data.get("subtitle_style", cls.__dataclass_fields__["subtitle_style"].default_factory()),
            )
            for asset in project.media:
                asset.available = Path(asset.path).is_file()
            project.validate()
            return project
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid project.json: {exc}") from exc

    @staticmethod
    def _media_from_dict(data: dict) -> MediaAsset:
        kind = MediaKind(data["kind"])
        defaults = {
            MediaKind.IMAGE: (MediaType.IMAGE, MediaCategory.NORMAL),
            MediaKind.VIDEO: (MediaType.VIDEO, MediaCategory.NORMAL),
            MediaKind.KEY_IMAGE: (MediaType.IMAGE, MediaCategory.KEY),
            MediaKind.SECTION_INTRO_VIDEO: (MediaType.VIDEO, MediaCategory.SECTION_INTRO),
        }
        media_type, category = defaults[kind]
        path = Path(data["path"])
        return MediaAsset(
            id=data["id"], kind=kind, path=str(path),
            section=SectionName(data["section"]) if data.get("section") else None,
            media_type=MediaType(data.get("media_type", media_type)),
            category=MediaCategory(data.get("category", category)),
            filename=data.get("filename", path.name), extension=data.get("extension", path.suffix.lower()),
            file_size=data.get("file_size", path.stat().st_size if path.is_file() else 0),
            width=data.get("width"), height=data.get("height"),
            duration_seconds=data.get("duration_seconds"), notes=data.get("notes", ""),
            available=path.is_file(),
        )
