"""TE Studio application state and atomic project/editor persistence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from te.media import MediaLibraryService
from te.project.manager import ProjectManager, _write_json
from te.project.models import (KeyPlacement, MediaCategory, MediaKind, MediaType, Project,
                               ScriptSegment, SectionName, TTSSettings, new_id)
from te.tts.sentences import SentenceSpeechService
from te.media.key_service import KeyPlacementService


STUDIO_VERSION = 1
MOTION_PRESETS = {
    "mixed": ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right"),
    "zoom": ("slow_zoom_in", "slow_zoom_out"),
    "pan": ("pan_left", "pan_right"),
    "none": ("none",),
}
TRANSITION_PRESETS = ("cut", "fade", "crossfade")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def _default_roots() -> tuple[Path, Path]:
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/TE-Projects"), Path("D:/TE-Output")
    home = Path.home()
    return home / "Documents" / "TE-Projects", home / "Videos" / "TE-Output"


def _safe_name(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not value:
        raise ValueError("Tên video không được để trống.")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                        *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value[:80]


def _sections_from_project(project: Project) -> dict[str, dict[str, str]]:
    result = {}
    for section in SectionName:
        segments = [segment for segment in project.segments if segment.section == section]
        references = [segment.vietnamese_reference for segment in segments]
        result[section.value] = {
            "ja": "\n\n".join(segment.japanese_text for segment in segments),
            "vi": "\n\n".join(reference or "" for reference in references)
                  if any(reference is not None for reference in references) else "",
        }
    return result


@dataclass
class StudioProject:
    directory: Path
    project: Project
    sections: dict[str, dict[str, str]]
    channel: str | None = None
    motion_preset: str = "mixed"
    transition: str = "cut"
    image_min_seconds: float = 7.0
    image_max_seconds: float = 12.0
    intro_preferences: dict[str, bool] = field(default_factory=lambda: {s.value: True for s in SectionName})
    key_drafts: list[dict] = field(default_factory=list)
    recovery_warning: str | None = None


class StudioService:
    def __init__(self, config_path: Path | None = None):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "TEStudio"
        self.config_path = Path(config_path) if config_path else base / "settings.json"
        projects_root, output_root = _default_roots()
        if self.config_path.is_file():
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if data.get("version") != STUDIO_VERSION:
                raise ValueError("Cấu hình TE Studio không tương thích.")
        else:
            data = {"version": STUDIO_VERSION, "projects_root": str(projects_root),
                    "output_root": str(output_root), "profiles": {}, "profile_aliases": {}}
        data.setdefault("profile_aliases", {})
        self.settings = data

    def save_settings(self) -> None:
        _write_json(self.config_path, self.settings)

    def set_directories(self, projects_root: Path, output_root: Path) -> None:
        self.settings["projects_root"] = str(Path(projects_root).expanduser().resolve())
        self.settings["output_root"] = str(Path(output_root).expanduser().resolve())
        self.save_settings()

    def profile_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.settings["profiles"], key=str.casefold))

    def create_profile(self, name: str, source: StudioProject | None = None) -> None:
        name = name.strip()
        if not name or name in self.settings["profiles"] or name in self.settings["profile_aliases"]:
            raise ValueError("Tên kênh trống hoặc đã tồn tại.")
        settings = deepcopy(source.project.tts_settings) if source else TTSSettings(
            provider="voicevox", voice_id="青山龍星", sample_rate=24000)
        self.settings["profiles"][name] = {
            "tts": asdict(settings),
            "subtitle": deepcopy(source.project.subtitle_style) if source else {},
            "motion_preset": source.motion_preset if source else "mixed",
            "transition": source.transition if source else "cut",
            "intro_preferences": deepcopy(source.intro_preferences) if source else
                {section.value: True for section in SectionName},
        }
        self.save_settings()

    def rename_profile(self, old: str, new: str) -> None:
        new = new.strip()
        if old not in self.settings["profiles"] or not new or new in self.settings["profiles"]:
            raise ValueError("Không thể đổi tên kênh.")
        self.settings["profiles"][new] = self.settings["profiles"].pop(old)
        aliases = self.settings["profile_aliases"]
        for alias, target in list(aliases.items()):
            if target == old:
                aliases[alias] = new
        aliases[old] = new
        self.save_settings()

    def save_profile_from_project(self, name: str, state: StudioProject) -> None:
        if name not in self.settings["profiles"]:
            raise KeyError(name)
        profile = self.settings["profiles"][name]
        profile.update(tts=asdict(state.project.tts_settings),
                       subtitle=deepcopy(state.project.subtitle_style),
                       motion_preset=state.motion_preset, transition=state.transition,
                       intro_preferences=deepcopy(state.intro_preferences))
        self.save_settings()

    def apply_profile(self, state: StudioProject, name: str) -> None:
        profile = self.settings["profiles"][name]
        state.channel = name
        state.project.tts_settings = TTSSettings(**profile["tts"])
        state.project.subtitle_style.update(profile.get("subtitle", {}))
        state.motion_preset = profile.get("motion_preset", "mixed")
        state.transition = profile.get("transition", "cut")
        state.intro_preferences = deepcopy(profile.get("intro_preferences", state.intro_preferences))
        # Media library is strictly project-specific and is never copied from a channel.

    def _directory_for(self, video_name: str, parent: Path | None = None) -> Path:
        root = Path(parent or self.settings["projects_root"]).expanduser().resolve()
        stem = _safe_name(video_name)
        candidate = root / stem
        index = 2
        while candidate.exists():
            candidate = root / f"{stem}-{index}"
            index += 1
        return candidate

    def create_project(self, video_name: str, *, parent: Path | None = None,
                       channel: str | None = None) -> StudioProject:
        directory = self._directory_for(video_name, parent)
        project = ProjectManager().create(directory, video_name.strip())
        state = StudioProject(directory, project, _sections_from_project(project))
        if channel:
            self.apply_profile(state, channel)
        else:
            project.tts_settings = TTSSettings(provider="voicevox", voice_id="青山龍星", sample_rate=24000)
        self.save_project(state)
        return state

    @staticmethod
    def _project_digest(directory: Path) -> str:
        return sha256((directory / "project.json").read_bytes()).hexdigest()

    def open_project(self, directory: Path) -> StudioProject:
        directory = Path(directory).resolve()
        project = ProjectManager().open(directory)
        sidecar = directory / "studio.json"
        if not sidecar.is_file():
            return StudioProject(directory, project, _sections_from_project(project))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if data.get("version") != STUDIO_VERSION:
            raise ValueError("Dữ liệu trình biên tập không tương thích.")
        sections = data.get("sections", _sections_from_project(project))
        warning = None
        if data.get("project_digest") != self._project_digest(directory):
            canonical = _sections_from_project(project)
            for section in SectionName:
                sections[section.value]["ja"] = canonical[section.value]["ja"]
                if canonical[section.value]["vi"]:
                    sections[section.value]["vi"] = canonical[section.value]["vi"]
            warning = "Đã khôi phục nội dung từ project.json sau lần lưu chưa hoàn tất."
        channel = data.get("channel")
        channel = self.settings["profile_aliases"].get(channel, channel)
        return StudioProject(directory, project, sections, channel,
            data.get("motion_preset", "mixed"), data.get("transition", "cut"),
            data.get("image_min_seconds", 7.0), data.get("image_max_seconds", 12.0),
            data.get("intro_preferences", {s.value: True for s in SectionName}),
            data.get("key_drafts", []), warning)

    @staticmethod
    def _apply_sections(project: Project, sections: dict[str, dict[str, str]]) -> None:
        changed: list[str] = []
        original = list(project.segments)
        updated: list[ScriptSegment] = []
        for section in SectionName:
            ja = sections[section.value]["ja"]
            vi = sections[section.value]["vi"]
            existing = [segment for segment in original if segment.section == section]
            if not ja.strip():
                if any(s.audio_ref or s.caption_refs or s.media_placement_refs for s in existing):
                    raise ValueError(f"{section}: cần giữ lời Nhật đã có audio, phụ đề hoặc hình; không thể xóa trắng.")
                removed = {s.id for s in existing}
                project.sentence_units = [u for u in project.sentence_units if u.segment_id not in removed]
                active = {u.id for u in project.sentence_units}
                project.sentence_audio_records = [r for r in project.sentence_audio_records if r.sentence_id in active]
                for key in project.key_placements:
                    if key.sentence_id not in active:
                        key.status = "manual_review"
                continue
            if not existing:
                updated.append(ScriptSegment(new_id("seg"), section, 0, ja, vi or None))
                changed.append(updated[-1].id)
            elif len(existing) == 1:
                segment = existing[0]
                if segment.japanese_text != ja:
                    segment.japanese_text = ja
                    changed.append(segment.id)
                segment.vietnamese_reference = vi or None
                updated.append(segment)
            else:
                old_ja = "\n\n".join(s.japanese_text for s in existing)
                old_vi = ("\n\n".join(s.vietnamese_reference or "" for s in existing)
                          if any(s.vietnamese_reference is not None for s in existing) else "")
                if ja != old_ja or vi != old_vi:
                    japanese_parts = ja.split("\n\n")
                    vietnamese_parts = vi.split("\n\n") if vi else [""] * len(existing)
                    if len(japanese_parts) != len(existing) or len(vietnamese_parts) != len(existing):
                        raise ValueError(f"{section}: dự án cũ có {len(existing)} đoạn; giữ cùng số đoạn để bảo toàn ID.")
                    for segment, japanese, vietnamese in zip(existing, japanese_parts, vietnamese_parts):
                        if not japanese.strip():
                            raise ValueError(f"{section}: đoạn lời Nhật trống.")
                        if segment.japanese_text != japanese:
                            segment.japanese_text = japanese
                            changed.append(segment.id)
                        segment.vietnamese_reference = vietnamese or None
                updated.extend(existing)
        for index, segment in enumerate(updated, 1):
            segment.number = index
        project.segments = updated
        project.validate()
        splitter = SentenceSpeechService()
        for segment in updated:
            if segment.id in changed or not any(unit.segment_id == segment.id for unit in project.sentence_units):
                splitter.split_segment(project, segment.id)

    def save_project(self, state: StudioProject) -> None:
        if not state.project.name.strip():
            raise ValueError("Tên video không được để trống.")
        if state.motion_preset not in MOTION_PRESETS or state.transition not in TRANSITION_PRESETS:
            raise ValueError("Hiệu ứng hoặc chuyển cảnh không hợp lệ.")
        if not 0 < state.image_min_seconds <= state.image_max_seconds <= 60:
            raise ValueError("Thời lượng ảnh phải nằm trong khoảng 0–60 giây.")
        working = deepcopy(state.project)
        self._apply_sections(working, state.sections)
        working.validate()
        ProjectManager().save(state.directory, working)
        state.project = working
        data = {"version": STUDIO_VERSION, "project_digest": self._project_digest(state.directory),
                "channel": state.channel, "sections": deepcopy(state.sections),
                "motion_preset": state.motion_preset, "transition": state.transition,
                "image_min_seconds": state.image_min_seconds,
                "image_max_seconds": state.image_max_seconds,
                "intro_preferences": deepcopy(state.intro_preferences),
                "key_drafts": deepcopy(state.key_drafts)}
        _write_json(state.directory / "studio.json", data)
        state.recovery_warning = None

    def rename_project(self, state: StudioProject, name: str) -> None:
        previous = state.project.name
        state.project.name = name.strip()
        try:
            self.save_project(state)
        except Exception:
            state.project.name = previous
            raise

    def duplicate_project(self, state: StudioProject, *, parent: Path | None = None,
                          name: str | None = None) -> StudioProject:
        self.save_project(state)
        new_name = (name or f"{state.project.name} - bản sao").strip()
        directory = self._directory_for(new_name, parent)
        new_project = ProjectManager().create(directory, new_name)
        clone = deepcopy(state.project)
        clone.id = new_project.id
        clone.name = new_name
        clone.audio_records.clear()
        clone.sentence_audio_records.clear()
        clone.subtitle_captions.clear()
        for segment in clone.segments:
            segment.audio_ref = None
            segment.caption_refs.clear()
        clone.validate()
        ProjectManager().save(directory, clone)
        duplicated = StudioProject(directory, clone, deepcopy(state.sections), state.channel,
            state.motion_preset, state.transition, state.image_min_seconds, state.image_max_seconds,
            deepcopy(state.intro_preferences), deepcopy(state.key_drafts))
        self.save_project(duplicated)
        return duplicated

    @staticmethod
    def set_section(state: StudioProject, section: SectionName, *, japanese: str,
                    vietnamese: str) -> None:
        state.sections[SectionName(section).value] = {"ja": japanese, "vi": vietnamese}

    def select_media_folder(self, state: StudioProject, path: Path) -> int:
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Cần chọn một thư mục ảnh.")
        working = deepcopy(state.project)
        working.media_library_path = str(path)
        MediaLibraryService().refresh_library(working)
        state.project = working
        self.save_project(state)
        return self.eligible_image_count(state)

    @staticmethod
    def eligible_image_count(state: StudioProject) -> int:
        return sum(asset.available and asset.media_type == MediaType.IMAGE and
                   asset.category == MediaCategory.NORMAL for asset in state.project.media)

    @staticmethod
    def media_folder_status(state: StudioProject) -> str:
        path = state.project.media_library_path
        if not path:
            return "Chưa chọn thư mục ảnh thường."
        if not Path(path).is_dir():
            return "Cảnh báo: thư mục ảnh thường không còn tồn tại."
        count = StudioService.eligible_image_count(state)
        return f"{count} ảnh thường phù hợp" if count else "Cảnh báo: thư mục chưa có ảnh thường phù hợp."

    def add_key_draft(self, state: StudioProject, path: Path) -> dict:
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("KEY phải là ảnh JPG, PNG hoặc WEBP.")
        working = deepcopy(state.project)
        asset = next((m for m in working.media if m.path == str(path)), None)
        if asset is None:
            asset = ProjectManager().add_media(working, MediaKind.KEY_IMAGE, path)
        elif asset.category != MediaCategory.KEY:
            try:
                asset = MediaLibraryService().mark_key(working, asset.id)
            except ValueError as exc:
                raise ValueError("Ảnh này đang được gán làm hình thường; hãy bỏ gán trước khi đặt KEY.") from exc
        if any(draft["media_id"] == asset.id for draft in state.key_drafts) or any(
                placement.media_id == asset.id for placement in working.key_placements):
            raise ValueError("Ảnh KEY này đã được chọn cho video.")
        state.project = working
        draft = {"id": uuid4().hex, "media_id": asset.id, "section": "P1", "sentence_id": None,
                 "vietnamese_text": "", "duration_seconds": None, "offset_ms": 0}
        state.key_drafts.append(draft)
        self.save_project(state)
        return draft

    def confirm_key_draft(self, state: StudioProject, draft_id: str, *, section: SectionName,
                          sentence_id: str, vietnamese_text: str, offset_ms: int = 0,
                          duration_seconds: float | None = None) -> KeyPlacement:
        draft = next(item for item in state.key_drafts if item["id"] == draft_id)
        unit = next((u for u in state.project.sentence_units if u.id == sentence_id), None)
        segment = next((s for s in state.project.segments if unit and s.id == unit.segment_id), None)
        if segment is None or segment.section != SectionName(section):
            raise ValueError("Câu Nhật không thuộc phần đã chọn.")
        duration_ms = round((duration_seconds if duration_seconds is not None else
                             state.image_min_seconds) * 1000)
        working = deepcopy(state.project)
        KeyPlacementService.confirm_mapping(working, state.directory, sentence_id, vietnamese_text)
        placement = KeyPlacementService.assign_key(working, state.directory, draft["media_id"],
            sentence_id, offset_ms=offset_ms, duration_ms=duration_ms)
        state.project = working
        state.key_drafts.remove(draft)
        self.save_project(state)
        return placement

    def remove_key(self, state: StudioProject, identifier: str) -> None:
        if any(item["id"] == identifier for item in state.key_drafts):
            state.key_drafts = [item for item in state.key_drafts if item["id"] != identifier]
        else:
            state.project.key_placements = [item for item in state.project.key_placements if item.id != identifier]
        self.save_project(state)

    def set_intro(self, state: StudioProject, section: SectionName, path: Path | None) -> None:
        working = deepcopy(state.project)
        section = SectionName(section)
        if path is None:
            working.section_intros[section.value] = None
        else:
            path = Path(path).expanduser().resolve(strict=True)
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("Intro phải là video MP4, MOV, MKV hoặc WEBM.")
            asset = next((m for m in working.media if m.path == str(path)), None)
            if asset is None:
                asset = ProjectManager().add_media(working, MediaKind.SECTION_INTRO_VIDEO, path, section)
            MediaLibraryService().assign_section_intro(working, section, asset.id)
        working.validate()
        state.project = working
        self.save_project(state)

    @staticmethod
    def intro_path(state: StudioProject, section: SectionName) -> str | None:
        media_id = state.project.section_intros[SectionName(section).value]
        return next((asset.path for asset in state.project.media if asset.id == media_id), None)
