import json
from pathlib import Path
import struct
import zlib

import pytest

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import AssignmentType, MediaCategory, MediaType, ProjectManager, SectionName


def write_png(path: Path, width: int = 8, height: int = 6) -> None:
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def build_library(root: Path) -> dict[str, bytes]:
    write_png(root / "normal" / "image001.png")
    write_png(root / "key" / "key01.jpg")
    (root / "normal" / "clip001.mp4").write_bytes(b"ordinary video")
    for section in SectionName:
        path = root / "section_intro" / f"{section}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"intro {section}".encode())
    (root / "normal" / "ignore.txt").write_text("unsupported", encoding="utf-8")
    return {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_scan_detects_images_videos_key_and_section_intros_without_changes(tmp_path):
    library = tmp_path / "2026-11-02_Ohtani"
    original = build_library(library)
    project = ProjectManager().create(tmp_path / "project", "Media")
    service = MediaLibraryService()
    result = service.scan_library(project, library)

    assert len(result.indexed) == 7
    assert any("Unsupported media skipped" in warning for warning in result.warnings)
    image = next(item for item in project.media if item.filename == "image001.png")
    video = next(item for item in project.media if item.filename == "clip001.mp4")
    key = next(item for item in project.media if item.filename == "key01.jpg")
    assert (image.media_type, image.category, image.width, image.height) == (MediaType.IMAGE, MediaCategory.NORMAL, 8, 6)
    assert (video.media_type, video.category) == (MediaType.VIDEO, MediaCategory.NORMAL)
    assert key.category == MediaCategory.KEY
    assert {item.section for item in project.media if item.category == MediaCategory.SECTION_INTRO} == set(SectionName)
    assert all(project.section_intros[section.value] for section in SectionName)
    assert original == {str(path): path.read_bytes() for path in library.rglob("*") if path.is_file()}


def test_refresh_preserves_ids_and_missing_references(tmp_path):
    library = tmp_path / "library"
    build_library(library)
    folder = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(folder, "Media")
    service = MediaLibraryService()
    service.scan_library(project, library)
    missing = next(item for item in project.media if item.filename == "image001.png")
    media_id = missing.id
    manager.save(folder, project)
    missing_path = Path(missing.path)
    missing_path.unlink()

    with pytest.warns(RuntimeWarning, match="references were preserved"):
        reopened = manager.open(folder)
    retained = next(item for item in reopened.media if item.id == media_id)
    assert retained.path == str(missing_path) and not retained.available
    report = service.validate_references(reopened)
    assert report.valid and any("unavailable" in warning for warning in report.warnings)
    refreshed = service.refresh_library(reopened)
    assert next(item for item in refreshed.indexed if item.id == media_id).available is False


def test_assignment_key_lock_unassign_and_persistence(tmp_path):
    library = tmp_path / "library"
    build_library(library)
    folder = tmp_path / "project"
    manager = ProjectManager()
    project = manager.create(folder, "Assignments")
    earlier = manager.add_segment(project, SectionName.P1, 1, "まだ秘密。")
    segment = manager.add_segment(project, SectionName.P1, 2, "公開の瞬間。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    key = next(item for item in project.media if item.category == MediaCategory.KEY)
    assignments = MediaAssignmentService()
    assignment = assignments.assign_media(
        project, segment.id, key.id, assignment_type=AssignmentType.KEY,
        locked=True, effect_preference="slow_zoom", notes="reveal only",
    )
    assert assignment.id in segment.media_placement_refs
    assert not assignments.is_media_allowed_at(project, key.id, earlier.id)
    assert assignments.is_media_allowed_at(project, key.id, segment.id)
    with pytest.raises(ValueError, match="locked"):
        assignments.unassign_media(project, segment.id)
    with pytest.raises(ValueError, match="Unassign"):
        media.unmark_key(project, key.id)
    manager.save(folder, project)
    reopened = manager.open(folder)
    saved = reopened.media_assignments[0]
    assert saved.locked and saved.assignment_type == AssignmentType.KEY
    assert saved.effect_preference == "slow_zoom"
    assignments.unassign_media(reopened, segment.id, force=True)
    media.unmark_key(reopened, key.id)
    assert not reopened.media_assignments


def test_assignment_and_intro_validation(tmp_path):
    library = tmp_path / "library"
    build_library(library)
    manager = ProjectManager()
    project = manager.create(tmp_path / "project", "Validation")
    p1 = manager.add_segment(project, SectionName.P1, 1, "一。")
    p2 = manager.add_segment(project, SectionName.P2, 2, "二。")
    media = MediaLibraryService()
    media.scan_library(project, library)
    normal = next(item for item in project.media if item.filename == "image001.png")
    p1_intro = next(item for item in project.media if item.section == SectionName.P1)
    assignments = MediaAssignmentService()
    with pytest.raises(ValueError, match="KEY"):
        assignments.assign_media(project, p1.id, normal.id, assignment_type=AssignmentType.KEY)
    with pytest.raises(ValueError, match="section"):
        assignments.assign_media(project, p2.id, p1_intro.id, assignment_type=AssignmentType.SECTION_INTRO)
    with pytest.raises(KeyError):
        assignments.assign_media(project, "seg_" + "0" * 32, normal.id)
    media.assign_section_intro(project, SectionName.P2, p1_intro.id)
    assert project.section_intros["P1"] is None
    assert project.section_intros["P2"] == p1_intro.id
    with pytest.raises(ValueError, match="video"):
        media.assign_section_intro(project, SectionName.P1, normal.id)


def test_manual_key_mark_and_stable_refresh_id(tmp_path):
    library = tmp_path / "library"
    write_png(library / "photo.webp")
    project = ProjectManager().create(tmp_path / "project", "Key")
    service = MediaLibraryService()
    service.scan_library(project, library)
    asset = project.media[0]
    service.mark_key(project, asset.id, notes="manual key")
    service.refresh_library(project)
    refreshed = project.media[0]
    assert refreshed.id == asset.id and refreshed.category == MediaCategory.KEY
    assert refreshed.notes == "manual key"


def test_video_outside_recommended_structure_can_be_an_intro(tmp_path):
    library = tmp_path / "library"
    path = library / "custom" / "opening.mov"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    project = ProjectManager().create(tmp_path / "project", "Manual intro")
    service = MediaLibraryService()
    service.scan_library(project, library)
    asset = project.media[0]
    assert asset.category == MediaCategory.NORMAL
    service.assign_section_intro(project, SectionName.P4, asset.id)
    assert asset.category == MediaCategory.SECTION_INTRO
    assert asset.section == SectionName.P4
    assert project.section_intros["P4"] == asset.id


def test_module1_v2_project_migrates_to_v3(tmp_path):
    manager = ProjectManager()
    folder = tmp_path / "project"
    project = manager.create(folder, "Old v2")
    manager.add_segment(project, SectionName.P1, 1, "旧データ。")
    manager.save(folder, project)
    path = folder / "project.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    old["schema_version"] = 2
    for key in ("media_library_path", "media_assignments", "section_intros", "media_reuse_distance"):
        old.pop(key)
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    opened = manager.open(folder)
    assert opened.schema_version == 8 and opened.media_library_path is None
    assert opened.section_intros == {section.value: None for section in SectionName}
    manager.save(folder, opened)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 8


@pytest.mark.parametrize(
    ("extension", "expected"),
    [(".jpg", MediaType.IMAGE), (".jpeg", MediaType.IMAGE), (".png", MediaType.IMAGE),
     (".webp", MediaType.IMAGE), (".mp4", MediaType.VIDEO), (".mov", MediaType.VIDEO),
     (".mkv", MediaType.VIDEO), (".webm", MediaType.VIDEO)],
)
def test_all_supported_extensions_are_indexed(tmp_path, extension, expected):
    library = tmp_path / "library"
    path = library / f"sample{extension}"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"dummy")
    project = ProjectManager().create(tmp_path / "project", "Formats")
    result = MediaLibraryService().scan_library(project, library)
    assert len(result.indexed) == 1
    assert result.indexed[0].media_type == expected
