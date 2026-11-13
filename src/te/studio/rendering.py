"""Studio's application workflow over the existing TTS, media, timeline and render services."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from contextlib import nullcontext
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

from te.media import KeyPlacementService, MediaAssignmentService, MediaLibraryService
from te.media.key_service import OrdinaryImageBag
from te.project.manager import ProjectManager
from te.project.models import AssignmentType, MediaCategory, MediaType, new_id
from te.render import RenderService, RenderSettings
from te.render.service import RenderError
from te.timeline import TimelineService
from te.tts import SentenceSpeechService, TTSService, VoicevoxProvider
from .service import MOTION_PRESETS, StudioProject, StudioService, _safe_name
from .multi_voice import MultiVoiceService, configuration_signature, speech_plan
from .performance import PerformanceReport


Progress = Callable[[str], None]


@contextmanager
def project_job_lock(directory: Path):
    """Advisory OS lock shared by Studio windows; automatically released on process exit."""
    path = Path(directory) / "generated" / "cache" / ".studio-job.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Dự án này đang tạo lời đọc hoặc render ở cửa sổ TE Studio khác.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class StudioRenderService:
    def __init__(self, studio: StudioService | None = None, render: RenderService | None = None,
                 speech: SentenceSpeechService | None = None, multi_voice: MultiVoiceService | None = None):
        self.studio = studio or StudioService()
        self.render = render or RenderService()
        self.speech = speech or SentenceSpeechService()
        self.multi_voice = multi_voice or MultiVoiceService()

    @staticmethod
    def _emit(progress: Progress | None, message: str) -> None:
        if progress:
            progress(message)

    @staticmethod
    def _font_family(state: StudioProject) -> str:
        configured = state.project.subtitle_style.get("font", "")
        return configured if configured and configured != "bold_japanese" else "Noto Sans CJK JP"

    @staticmethod
    def _audio_problem(project) -> str | None:
        tts = TTSService()
        for segment in project.segments:
            status = tts.get_audio_status(project, segment.id)
            if status != "ready":
                return f"Lời đọc của đoạn {segment.number} {status}; hãy bấm 'Tạo / cập nhật lời đọc'."
            units = [unit for unit in project.sentence_units if unit.segment_id == segment.id]
            if not units:
                return f"Đoạn {segment.number} chưa có câu lời đọc; hãy tạo lại lời đọc."
            for unit in units:
                status = SentenceSpeechService._sentence_status(
                    project, unit, segment.tts_override or project.tts_settings)
                if status != "ready":
                    return f"Câu {unit.order} của đoạn {segment.number} {status}; hãy cập nhật lời đọc."
        return None

    def validate_inputs(self, state: StudioProject, *, need_audio: bool = True,
                        need_render_tools: bool = True) -> None:
        project = state.project
        if not project.name.strip():
            raise ValueError("Nhập tên video trước khi tiếp tục.")
        if not project.segments or any(not segment.japanese_text.strip() for segment in project.segments):
            raise ValueError("Cần có lời dẫn tiếng Nhật hợp lệ trong ít nhất một phần P1–P4.")
        if not need_audio and not need_render_tools:
            return
        if not project.media_library_path or not Path(project.media_library_path).is_dir():
            raise ValueError("Thư mục ảnh thường chưa chọn hoặc không còn tồn tại.")
        if not any(item.category == MediaCategory.NORMAL and item.media_type == MediaType.IMAGE
                   and Path(item.path).is_file() for item in project.media):
            raise ValueError("Thư mục ảnh thường không có ảnh JPG, PNG hoặc WebP khả dụng.")
        for section, media_id in project.section_intros.items():
            if media_id and state.intro_preferences.get(section, True):
                asset = next((item for item in project.media if item.id == media_id), None)
                if asset is None or not Path(asset.path).is_file():
                    raise ValueError(f"Intro {section} bị thiếu; hãy chọn lại hoặc tắt mục 'Dùng'.")
                if asset.media_type != MediaType.VIDEO or not asset.duration_seconds or asset.duration_seconds <= 0:
                    raise ValueError(f"Intro {section} không đọc được thời lượng video; hãy chọn file khác.")
        if state.key_drafts:
            raise ValueError("Còn ảnh KEY chưa xác nhận. Hãy xác nhận hoặc xóa bản nháp KEY.")
        for placement in project.key_placements:
            asset = next((item for item in project.media if item.id == placement.media_id), None)
            if asset is None or not Path(asset.path).is_file():
                raise ValueError("Ảnh KEY bị thiếu; hãy chọn lại trước khi render.")
            unit = next((unit for unit in project.sentence_units if unit.id == placement.sentence_id), None)
            if (placement.status != "ready" or unit is None or not unit.mapping_confirmed or
                    unit.vietnamese_reference != placement.confirmed_vietnamese_text):
                raise ValueError("Cặp câu VN–JP của KEY cần xem lại; hãy xác nhận lại trước khi render.")
        if need_audio:
            if state.audio_mode != state.narration_mode:
                raise ValueError("Chế độ lời đọc đã đổi; hãy cập nhật WAV trước khi render.")
            if (state.narration_mode == "multi" and
                    state.audio_configuration_signature != configuration_signature(state)):
                raise ValueError("Giọng hoặc phân vai đã đổi; hãy cập nhật WAV đa giọng trước khi render.")
            if problem := self._audio_problem(project):
                raise ValueError(problem)
            subtitle = TimelineService().subtitles
            if any(subtitle.detect_stale_alignment(project, segment.id) or
                   not subtitle._captions(project, segment.id) for segment in project.segments):
                raise ValueError("Phụ đề chưa đồng bộ với lời đọc; hãy cập nhật lời đọc.")
        if need_render_tools:
            self.render.check_ffmpeg()
            self.render._resolve_font(RenderSettings(font_family=self._font_family(state),
                                                      font_path=state.font_path))

    def generate_narration(self, state: StudioProject, progress: Progress | None = None) -> int:
        with project_job_lock(state.directory):
            return self._generate_narration_locked(state, progress)

    def _generate_narration_locked(self, state: StudioProject, progress: Progress | None,
                                   performance: PerformanceReport | None = None) -> int:
        self.validate_inputs(state, need_audio=False, need_render_tools=False)
        if state.narration_mode == "multi":
            speech_plan(state)  # Reject stale/overlapping attribution before generating any WAV.
        working = deepcopy(state.project)
        count = 0
        try:
            if state.narration_mode == "multi":
                manifest, warnings, count = self.multi_voice.prepare(state, working, progress, performance)
                for warning in warnings:
                    self._emit(progress, "Cảnh báo: " + warning)
            elif state.audio_mode == "multi":
                working.sentence_audio_records.clear()
                working.audio_records.clear()
                for segment in working.segments:
                    segment.audio_ref = None
            for index, segment in enumerate(working.segments, 1):
                units = [unit for unit in working.sentence_units if unit.segment_id == segment.id]
                settings = segment.tts_override or working.tts_settings
                with performance.measure("wav_cache_lookup") if performance else nullcontext():
                    needs_generation = state.narration_mode == "single" and (not units or any(
                        self.speech._sentence_status(working, unit, settings) != "ready" for unit in units)
                    )
                if needs_generation and settings.provider == "voicevox":
                    with performance.measure("voicevox_setup") if performance else nullcontext():
                        VoicevoxProvider(timeout=5).check_connection(settings)
                self._emit(progress, f"Lời đọc {index}/{len(working.segments)}: {segment.section.value}")
                line_limit = self.render.caption_line_limit(RenderSettings(subtitle_font_size=state.subtitle_size),
                    working.subtitle_style["max_chars_per_line"])
                self.speech.sync_segment(working, state.directory, segment.id, rebuild_timeline=False,
                                         caption_max_chars=line_limit, performance=performance)
                count += int(needs_generation)
        except Exception:
            state.project = ProjectManager().open(state.directory)
            self.studio.save_project(state)
            raise
        state.project = working
        state.audio_mode = state.narration_mode
        state.multi_voice_manifest = manifest if state.narration_mode == "multi" else {}
        state.audio_configuration_signature = (configuration_signature(state)
                                               if state.narration_mode == "multi" else None)
        self.studio.save_project(state)
        unit_name = "câu" if state.narration_mode == "multi" else "đoạn"
        self._emit(progress, f"Đã đồng bộ {len(working.segments)} đoạn; {count} {unit_name} cần tạo WAV mới.")
        return count

    @staticmethod
    def _distribute_images(project, timeline, minimum: float, maximum: float) -> None:
        """Split long ordinary intervals without moving KEY or section timing anchors."""
        candidates = [item.id for item in project.media if item.available and
                      item.category == MediaCategory.NORMAL and item.media_type == MediaType.IMAGE]
        bag = OrdinaryImageBag(candidates, project.random_seed)
        keys = {item.id for item in project.key_placements}
        media = {item.id: item for item in project.media}
        fixed = {item.segment_id for item in project.media_assignments
                 if item.locked or item.assignment_type not in (AssignmentType.RANDOM, AssignmentType.FUTURE_AI)}
        result = []
        for placement in sorted(timeline.placements, key=lambda item: (item.start_ms, item.end_ms)):
            if placement.id in keys or media[placement.media_id].category != MediaCategory.NORMAL:
                result.append(placement)
                continue
            primary_id = placement.media_id
            if (len(candidates) > 1 and primary_id == bag.last and
                    placement.segment_id not in fixed):
                primary_id = bag.draw()
            else:
                bag.observe(primary_id)
            duration = placement.end_ms - placement.start_ms
            lower = max(1, math.ceil(duration / (maximum * 1000)))
            upper = max(1, math.floor(duration / (minimum * 1000)))
            ideal = max(1, round(duration / ((minimum + maximum) * 500)))
            chunks = max(lower, min(ideal, upper)) if upper >= lower else lower
            if placement.segment_id in fixed:
                chunks = 1
            if chunks == 1:
                piece = deepcopy(placement)
                piece.media_id = primary_id
                result.append(piece)
                continue
            for index in range(chunks):
                start = placement.start_ms + round(duration * index / chunks)
                end = placement.start_ms + round(duration * (index + 1) / chunks)
                if end <= start:
                    continue
                piece = deepcopy(placement)
                piece.id = placement.id if index == 0 else new_id("plc")
                piece.start_ms, piece.end_ms = start, end
                piece.media_id = bag.draw() if index else primary_id
                result.append(piece)
        timeline.placements = sorted(result, key=lambda item: (item.start_ms, item.end_ms))

    def prepare_timeline(self, state: StudioProject, progress: Progress | None = None):
        project = deepcopy(state.project)
        MediaLibraryService().refresh_library(project)
        inspected = deepcopy(state)
        inspected.project = project
        self.validate_inputs(inspected)
        media = {item.id: item for item in project.media}
        library_root = Path(project.media_library_path)
        for assignment in list(project.media_assignments):
            asset = media.get(assignment.media_id)
            valid = (asset is not None and asset.available and asset.media_type == MediaType.IMAGE
                     and asset.category == MediaCategory.NORMAL and
                     Path(asset.path).is_relative_to(library_root))
            if valid:
                continue
            if assignment.assignment_type == AssignmentType.RANDOM and not assignment.locked:
                MediaAssignmentService().unassign_media(project, assignment.segment_id)
            else:
                segment = next(item for item in project.segments if item.id == assignment.segment_id)
                raise ValueError(f"Hình đã gán cho đoạn {segment.number} không còn trong thư mục ảnh thường; "
                                 "hãy chọn lại hoặc bỏ khóa hình đó.")
        self._emit(progress, "Đang gán ảnh thường…")
        KeyPlacementService.assign_random_images(project, state.directory, persist=False)
        timeline_service = TimelineService()
        previous = None
        try:
            previous = timeline_service.load_timeline(project, state.directory)
        except (OSError, ValueError, KeyError):
            pass
        self._emit(progress, "Đang tính timeline từ thời lượng WAV thực…")
        timeline, warnings = timeline_service.build_timeline(project, state.directory,
            previous=previous, effect_sequence=MOTION_PRESETS[state.motion_preset],
            intro_overlap=True, intro_enabled=state.intro_preferences)
        for transition in timeline.transitions:
            transition.preset = state.transition
        self._distribute_images(project, timeline, state.image_min_seconds, state.image_max_seconds)
        timeline_service.save_timeline(project, state.directory, timeline)
        self.render.validate_render_inputs(project, timeline, RenderSettings(
            960, 540, 30, 28, "veryfast", font_family=self._font_family(state),
            font_path=state.font_path, subtitle_font_size=state.subtitle_size))
        state.project = project
        self.studio.save_project(state)
        for warning in warnings:
            self._emit(progress, f"Cảnh báo: {warning}")
        return timeline

    @staticmethod
    def _publish(source: Path, output_root: Path, name: str) -> Path:
        output_root.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(name, max_length=220, max_bytes=230)
        partial = output_root / f".te-{uuid4().hex}.partial.mp4"
        try:
            shutil.copyfile(source, partial)
            version = 1
            while True:
                destination = output_root / (f"{stem}.mp4" if version == 1 else f"{stem}_v{version}.mp4")
                try:
                    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    version += 1
                    continue
                else:
                    os.close(descriptor)
                    break
            try:
                os.replace(partial, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        finally:
            partial.unlink(missing_ok=True)
        return destination

    def render_video(self, state: StudioProject, *, preview: bool,
                     progress: Progress | None = None) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = state.directory / "generated" / "output" / "diagnostics" / (
            f"performance-{stamp}-{'preview' if preview else 'full'}.json")
        report = PerformanceReport(path, state.project.id, preview=preview,
                                   narration_mode=state.narration_mode)
        report.resolution = [960, 540] if preview else [1920, 1080]
        error = None
        try:
            with project_job_lock(state.directory):
                result = self._render_video_locked(state, preview=preview, progress=progress,
                                                   performance=report)
        except Exception as exc:
            error = exc
            raise
        finally:
            status = ("cancelled" if isinstance(error, RenderError) and self.render._cancel.is_set()
                      else "failed") if error else "success"
            try:
                report.finish(status, error)
            except OSError:
                pass  # A diagnostics write must not hide the original render result.
        return result

    def _render_video_locked(self, state: StudioProject, *, preview: bool,
                             progress: Progress | None = None,
                             performance: PerformanceReport | None = None) -> Path:
        self._emit(progress, "Đang kiểm tra kịch bản, ảnh, KEY, intro, FFmpeg và phông Nhật…")
        with performance.measure("asset_timeline_preparation") if performance else nullcontext():
            inspected = deepcopy(state)
            MediaLibraryService().refresh_library(inspected.project)
            self.validate_inputs(inspected, need_audio=False)
        self._emit(progress, "Đang kiểm tra và cập nhật WAV/phụ đề theo từng câu…")
        self._generate_narration_locked(state, progress, performance)
        with performance.measure("asset_timeline_preparation") if performance else nullcontext():
            timeline = self.prepare_timeline(state, progress)
        if performance:
            performance.video_duration_seconds = max(
                [item.end_ms for item in timeline.blocks] +
                [item.end_ms for item in timeline.intros] +
                [item.end_ms for item in timeline.placements], default=0) / 1000
        settings = (RenderSettings(960, 540, 30, 28, "veryfast", font_path=state.font_path)
                    if preview else RenderSettings(font_path=state.font_path, encoder="auto"))
        settings = replace(settings, font_family=self._font_family(state))
        settings = replace(settings, subtitle_font_size=state.subtitle_size)
        if performance:
            performance.resolution = [settings.width, settings.height]
        self._emit(progress, "Đang render bản xem nhanh…" if preview else "Đang render Full HD…")
        source = self.render.render_project(state.project, timeline, state.directory, preview=preview,
            overwrite=True, settings=settings, progress=progress, performance=performance)
        if preview:
            return source
        destination = self._publish(source, Path(self.studio.settings["output_root"]), state.project.name)
        state.last_final = str(destination)
        self.studio.save_project(state)
        self._emit(progress, f"Video hoàn tất: {destination}")
        return destination
