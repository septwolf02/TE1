"""Project persistence. Source media is referenced, never copied or written."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import warnings

from .models import (
    MediaAsset, MediaCategory, MediaKind, MediaType, Project, ScriptSegment, SectionName, new_id,
)
from te.timeline.models import Timeline


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ProjectManager:
    def create(self, directory: Path, name: str) -> Project:
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()):
            raise FileExistsError(f"Project directory is not empty: {directory}")
        project = Project(id=new_id("prj"), name=name)
        project.validate()
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "project.json", project.to_dict())
        _write_json(directory / project.timeline_file, Timeline(project_id=project.id).to_dict())
        return project

    def open(self, directory: Path) -> Project:
        directory = Path(directory)
        with (directory / "project.json").open(encoding="utf-8") as stream:
            project = Project.from_dict(json.load(stream))
        with (directory / project.timeline_file).open(encoding="utf-8") as stream:
            Timeline.from_dict(json.load(stream), project)
        missing = [asset.path for asset in project.media if not asset.available]
        if missing:
            warnings.warn(
                f"{len(missing)} media source file(s) are unavailable; references were preserved",
                RuntimeWarning,
                stacklevel=2,
            )
        return project

    def save(self, directory: Path, project: Project) -> None:
        directory = Path(directory)
        existing = self.open(directory)
        if existing.id != project.id:
            raise ValueError("Project ID does not match destination")
        with (directory / project.timeline_file).open(encoding="utf-8") as stream:
            Timeline.from_dict(json.load(stream), project)
        _write_json(directory / "project.json", project.to_dict())

    def add_segment(self, project: Project, section: SectionName, number: int,
                    japanese_text: str, vietnamese_reference: str | None = None) -> ScriptSegment:
        segment = ScriptSegment(new_id("seg"), SectionName(section), number, japanese_text, vietnamese_reference)
        project.segments.append(segment)
        try:
            project.validate()
        except Exception:
            project.segments.pop()
            raise
        return segment

    def edit_segment(self, project: Project, segment_id: str, *, japanese_text: str | None = None,
                     vietnamese_reference: str | None = None) -> ScriptSegment:
        segment = next((s for s in project.segments if s.id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        old = (segment.japanese_text, segment.vietnamese_reference)
        if japanese_text is not None:
            segment.japanese_text = japanese_text
        if vietnamese_reference is not None:
            segment.vietnamese_reference = vietnamese_reference
        try:
            project.validate()
        except Exception:
            segment.japanese_text, segment.vietnamese_reference = old
            raise
        return segment

    def add_media(self, project: Project, kind: MediaKind, path: Path,
                  section: SectionName | None = None) -> MediaAsset:
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Media must be a file")
        asset = MediaAsset(new_id("med"), MediaKind(kind), str(path),
                           SectionName(section) if section else None,
                           MediaType.VIDEO if MediaKind(kind) in (MediaKind.VIDEO, MediaKind.SECTION_INTRO_VIDEO) else MediaType.IMAGE,
                           MediaCategory.KEY if MediaKind(kind) == MediaKind.KEY_IMAGE else
                           MediaCategory.SECTION_INTRO if MediaKind(kind) == MediaKind.SECTION_INTRO_VIDEO else MediaCategory.NORMAL,
                           path.name, path.suffix.lower(), path.stat().st_size)
        project.media.append(asset)
        try:
            project.validate()
        except Exception:
            project.media.pop()
            raise
        return asset
