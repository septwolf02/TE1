"""Local media indexing and primary visual assignment services."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import struct
import subprocess

from te.project.models import (
    AssignmentType, MediaAsset, MediaAssignment, MediaCategory, MediaKind, MediaType,
    Project, SectionName, SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS, new_id,
)


@dataclass(frozen=True)
class MediaScanResult:
    indexed: tuple[MediaAsset, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceValidation:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read common image headers without loading or changing the image."""
    try:
        with path.open("rb") as stream:
            header = stream.read(30)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return struct.unpack(">II", header[16:24])
            if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                if header[12:16] == b"VP8X" and len(header) >= 30:
                    return (1 + int.from_bytes(header[24:27], "little"),
                            1 + int.from_bytes(header[27:30], "little"))
            if header.startswith(b"\xff\xd8"):
                stream.seek(2)
                sof_markers = set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0))
                while True:
                    marker_start = stream.read(1)
                    if not marker_start:
                        break
                    if marker_start != b"\xff":
                        continue
                    marker = stream.read(1)
                    while marker == b"\xff":
                        marker = stream.read(1)
                    if marker and marker[0] in sof_markers:
                        stream.read(3)
                        height, width = struct.unpack(">HH", stream.read(4))
                        return width, height
                    length_data = stream.read(2)
                    if len(length_data) != 2:
                        break
                    stream.seek(int.from_bytes(length_data, "big") - 2, 1)
    except (OSError, ValueError, struct.error):
        pass
    return None, None


def _video_metadata(path: Path) -> tuple[int | None, int | None, float | None]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None, None, None
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        data = json.loads(completed.stdout)
        stream = data.get("streams", [{}])[0]
        duration = data.get("format", {}).get("duration")
        return stream.get("width"), stream.get("height"), float(duration) if duration is not None else None
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, IndexError):
        return None, None, None


class MediaLibraryService:
    def scan_library(self, project: Project, folder: Path) -> MediaScanResult:
        root = Path(folder).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Media library must be a directory")
        if project.media_library_path and Path(project.media_library_path) != root and project.media:
            raise ValueError("Clear the current media library before selecting another folder")
        project.media_library_path = str(root)
        return self.refresh_library(project)

    def refresh_library(self, project: Project) -> MediaScanResult:
        if project.media_library_path is None:
            raise ValueError("No media library selected")
        root = Path(project.media_library_path)
        existing = {Path(asset.path): asset for asset in project.media}
        warnings: list[str] = []
        seen: set[Path] = set()
        if root.is_dir():
            paths = sorted((item.resolve() for item in root.rglob("*") if item.is_file()), key=str)
            for path in paths:
                extension = path.suffix.lower()
                if extension not in SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS:
                    warnings.append(f"Unsupported media skipped: {path}")
                    continue
                seen.add(path)
                asset = existing.get(path)
                detected = self._index(path, root, asset.id if asset else new_id("med"), asset.notes if asset else "")
                if asset and asset.category == MediaCategory.KEY and detected.media_type == MediaType.IMAGE:
                    detected.category = MediaCategory.KEY
                    detected.kind = MediaKind.KEY_IMAGE
                existing[path] = detected
        else:
            warnings.append(f"Media library folder is unavailable: {root}")
        for path, asset in existing.items():
            if path not in seen:
                asset.available = False
                warnings.append(f"Media file is unavailable: {path}")
        project.media = sorted(existing.values(), key=lambda asset: asset.path.casefold())
        self._sync_detected_intros(project, warnings)
        project.validate()
        return MediaScanResult(tuple(project.media), tuple(warnings))

    def list_media(self, project: Project, *, available_only: bool = False) -> tuple[MediaAsset, ...]:
        return tuple(asset for asset in project.media if asset.available or not available_only)

    def list_assets(self, project: Project) -> tuple[MediaAsset, ...]:
        """Compatibility with the replaceable MediaLibrary port."""
        return self.list_media(project)

    def mark_key(self, project: Project, media_id: str, *, notes: str | None = None) -> MediaAsset:
        asset = self._media(project, media_id)
        if asset.media_type != MediaType.IMAGE or asset.category == MediaCategory.SECTION_INTRO:
            raise ValueError("Only ordinary images can be marked KEY")
        if any(a.media_id == media_id and a.assignment_type not in (AssignmentType.KEY, AssignmentType.FUTURE_AI)
               for a in project.media_assignments):
            raise ValueError("Reassign this media as KEY before marking it KEY")
        asset.kind = MediaKind.KEY_IMAGE
        asset.category = MediaCategory.KEY
        if notes is not None:
            asset.notes = notes
        project.validate()
        return asset

    def unmark_key(self, project: Project, media_id: str) -> MediaAsset:
        if any(a.media_id == media_id for a in project.media_assignments):
            raise ValueError("Unassign KEY media before unmarking it")
        asset = self._media(project, media_id)
        if asset.category == MediaCategory.KEY:
            asset.kind = MediaKind.IMAGE
            asset.category = MediaCategory.NORMAL
        project.validate()
        return asset

    def assign_section_intro(self, project: Project, section: SectionName, media_id: str | None) -> None:
        section = SectionName(section)
        if media_id is not None:
            asset = self._media(project, media_id)
            if asset.media_type != MediaType.VIDEO:
                raise ValueError("Section intro must be a video")
            if any(a.media_id == media_id and a.assignment_type != AssignmentType.SECTION_INTRO
                   for a in project.media_assignments):
                raise ValueError("Reassign this media as a section intro first")
            if asset.category == MediaCategory.SECTION_INTRO and asset.section != section:
                if project.section_intros.get(asset.section.value) == asset.id:
                    project.section_intros[asset.section.value] = None
            asset.kind = MediaKind.SECTION_INTRO_VIDEO
            asset.category = MediaCategory.SECTION_INTRO
            asset.section = section
        project.section_intros[section.value] = media_id
        project.validate()

    def validate_references(self, project: Project) -> ReferenceValidation:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            project.validate()
        except ValueError as exc:
            errors.append(str(exc))
        for asset in project.media:
            if not Path(asset.path).is_file():
                warnings.append(f"Media file is unavailable: {asset.path}")
        for assignment in project.media_assignments:
            asset = next((item for item in project.media if item.id == assignment.media_id), None)
            if asset is not None and not asset.available:
                label = "KEY assignment" if assignment.assignment_type == AssignmentType.KEY else "Assignment"
                warnings.append(f"{label} media is unavailable: {asset.path}")
        return ReferenceValidation(tuple(errors), tuple(dict.fromkeys(warnings)))

    @staticmethod
    def _index(path: Path, root: Path, media_id: str, notes: str) -> MediaAsset:
        extension = path.suffix.lower()
        media_type = MediaType.IMAGE if extension in SUPPORTED_IMAGE_EXTENSIONS else MediaType.VIDEO
        parent_names = {part.casefold() for part in path.relative_to(root).parts[:-1]}
        section = None
        category = MediaCategory.NORMAL
        if media_type == MediaType.VIDEO and "section_intro" in parent_names and path.stem.upper() in {s.value for s in SectionName}:
            category = MediaCategory.SECTION_INTRO
            section = SectionName(path.stem.upper())
        elif media_type == MediaType.IMAGE and ("key" in parent_names or path.stem.casefold().startswith("key")):
            category = MediaCategory.KEY
        kind = {
            (MediaType.IMAGE, MediaCategory.NORMAL): MediaKind.IMAGE,
            (MediaType.VIDEO, MediaCategory.NORMAL): MediaKind.VIDEO,
            (MediaType.IMAGE, MediaCategory.KEY): MediaKind.KEY_IMAGE,
            (MediaType.VIDEO, MediaCategory.SECTION_INTRO): MediaKind.SECTION_INTRO_VIDEO,
        }[(media_type, category)]
        if media_type == MediaType.IMAGE:
            width, height = _image_dimensions(path)
            duration = None
        else:
            width, height, duration = _video_metadata(path)
        return MediaAsset(
            id=media_id, kind=kind, path=str(path), section=section, media_type=media_type,
            category=category, filename=path.name, extension=extension, file_size=path.stat().st_size,
            width=width, height=height, duration_seconds=duration, notes=notes, available=True,
        )

    @staticmethod
    def _sync_detected_intros(project: Project, warnings: list[str]) -> None:
        for section in SectionName:
            choices = [asset for asset in project.media if asset.available and
                       asset.category == MediaCategory.SECTION_INTRO and asset.section == section]
            current = project.section_intros.get(section.value)
            if current is None and choices:
                project.section_intros[section.value] = choices[0].id
            if len(choices) > 1:
                warnings.append(f"Multiple intro clips detected for {section}; using the current assignment")

    @staticmethod
    def _media(project: Project, media_id: str) -> MediaAsset:
        try:
            return next(asset for asset in project.media if asset.id == media_id)
        except StopIteration:
            raise KeyError(media_id) from None


class MediaAssignmentService:
    def assign_media(self, project: Project, segment_id: str, media_id: str, *,
                     assignment_type: AssignmentType = AssignmentType.MANUAL, locked: bool = False,
                     effect_preference: str | None = None, notes: str = "") -> MediaAssignment:
        segment = self._segment(project, segment_id)
        asset = MediaLibraryService._media(project, media_id)
        assignment_type = AssignmentType(assignment_type)
        if any(item.segment_id == segment_id for item in project.media_assignments):
            raise ValueError("Segment already has a primary visual assignment")
        if assignment_type == AssignmentType.KEY and asset.category != MediaCategory.KEY:
            raise ValueError("KEY assignment requires KEY media")
        if assignment_type == AssignmentType.SECTION_INTRO and (
            asset.category != MediaCategory.SECTION_INTRO or asset.section != segment.section
        ):
            raise ValueError("Section intro does not match segment section")
        assignment = MediaAssignment(new_id("plc"), segment_id, media_id, assignment_type,
                                     locked, effect_preference, notes)
        project.media_assignments.append(assignment)
        segment.media_placement_refs.append(assignment.id)
        try:
            project.validate()
        except Exception:
            project.media_assignments.pop()
            segment.media_placement_refs.pop()
            raise
        return assignment

    def unassign_media(self, project: Project, segment_id: str, *, force: bool = False) -> None:
        assignment = next((item for item in project.media_assignments if item.segment_id == segment_id), None)
        if assignment is None:
            return
        if assignment.locked and not force:
            raise ValueError("Assignment is locked")
        segment = self._segment(project, segment_id)
        project.media_assignments.remove(assignment)
        if assignment.id in segment.media_placement_refs:
            segment.media_placement_refs.remove(assignment.id)
        project.validate()

    def set_locked(self, project: Project, segment_id: str, locked: bool) -> MediaAssignment:
        assignment = next((item for item in project.media_assignments if item.segment_id == segment_id), None)
        if assignment is None:
            raise KeyError(segment_id)
        assignment.locked = locked
        project.validate()
        return assignment

    def is_media_allowed_at(self, project: Project, media_id: str, segment_id: str) -> bool:
        """Guard future selectors from using KEY media before its assigned reveal segment."""
        asset = MediaLibraryService._media(project, media_id)
        target = self._segment(project, segment_id)
        if asset.category != MediaCategory.KEY:
            return True
        reveal = next((item for item in project.media_assignments if item.media_id == media_id), None)
        if reveal is None:
            return False
        reveal_segment = self._segment(project, reveal.segment_id)
        return target.number >= reveal_segment.number

    @staticmethod
    def _segment(project: Project, segment_id: str):
        try:
            return next(segment for segment in project.segments if segment.id == segment_id)
        except StopIteration:
            raise KeyError(segment_id) from None
