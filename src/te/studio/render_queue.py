"""Persistent single-worker queue around the existing Studio render workflow."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from te.project.manager import _write_json
from te.project.models import Project, SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS
from te.render import RenderService, RenderSettings
from te.timeline.models import Timeline
from .multi_voice import speech_plan
from .rendering import StudioRenderService, project_job_lock
from .service import StudioProject, StudioService


class QueueCancelled(RuntimeError):
    pass


@contextmanager
def _file_lock(path: Path, *, blocking: bool = True):
    """Short state locks and one long worker lock, including across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            raise RuntimeError("Hàng đợi Render đang được xử lý trong cửa sổ TE Studio khác.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_digest(manifest: dict) -> str:
    return sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _asset_paths(state: StudioProject) -> list[Path]:
    project = state.project
    paths = {Path(asset.path).resolve() for asset in project.media}
    if state.font_path:
        paths.add(Path(state.font_path).resolve())
    font_settings = RenderSettings(font_family=StudioRenderService._font_family(state),
                                   font_path=state.font_path)
    _, resolved_font = RenderService._resolve_font(font_settings)
    if resolved_font:
        paths.add(resolved_font.resolve())
    root = Path(project.media_library_path) if project.media_library_path else None
    if root and root.is_dir():
        extensions = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
        disk = {path.resolve() for path in root.rglob("*") if path.is_file() and
                path.suffix.lower() in extensions}
        indexed = {Path(asset.path).resolve() for asset in project.media
                   if Path(asset.path).resolve().is_relative_to(root.resolve()) and
                   Path(asset.path).suffix.lower() in extensions}
        if disk != indexed:
            raise ValueError("Thư mục ảnh thay đổi so với danh sách project; hãy tải lại ảnh trước khi xếp hàng.")
        paths.update(disk)
    return sorted(paths, key=str)


def _asset_fingerprints(state: StudioProject) -> dict[str, dict]:
    result = {}
    for path in _asset_paths(state):
        if not path.is_file():
            raise ValueError(f"Media hoặc font đã thiếu: {path}")
        before = path.stat()
        digest = _hash_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Media thay đổi khi tạo snapshot: {path}")
        result[str(path)] = {"size": after.st_size, "sha256": digest}
    return result


def _rebase_audio(value, old_audio: Path, new_audio: Path):
    if isinstance(value, dict):
        return {key: _rebase_audio(item, old_audio, new_audio) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_audio(item, old_audio, new_audio) for item in value]
    if isinstance(value, str):
        old = str(old_audio)
        normalized = os.path.normcase(value)
        prefix = os.path.normcase(old)
        if normalized == prefix:
            return str(new_audio)
        if normalized.startswith(prefix + os.sep):
            return str(new_audio / value[len(old) + 1:])
    return value


class RenderQueue:
    """Queue metadata is machine-local; each job owns a frozen project workspace."""

    def __init__(self, studio: StudioService, *, renderer_factory: Callable | None = None,
                 on_change: Callable[[], None] | None = None):
        self.studio = studio
        self.root = studio.config_path.parent / "render-queue"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "queue.json"
        self.state_lock = self.root / "queue-state.lock"
        self.worker_lock = self.root / "queue-worker.lock"
        self.renderer_factory = renderer_factory or StudioRenderService
        self.on_change = on_change or (lambda: None)
        self._thread: Thread | None = None
        self._renderer: StudioRenderService | None = None
        self._cancel = Event()
        self._pause = Event()
        self._local = Lock()
        self._auto_continue = False
        self.last_error: str | None = None
        self._recover()

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"version": 1, "jobs": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("jobs"), list):
            raise ValueError("Dữ liệu hàng đợi Render không hợp lệ.")
        return data

    def _change(self, change):
        with _file_lock(self.state_lock):
            data = self._read()
            result = change(data["jobs"])
            _write_json(self.path, data)
        self.on_change()
        return result

    def jobs(self) -> list[dict]:
        with _file_lock(self.state_lock):
            return deepcopy(self._read()["jobs"])

    def _recover(self) -> None:
        try:
            with _file_lock(self.worker_lock, blocking=False):
                if any(job["status"] == "running" for job in self.jobs()):
                    self._change(lambda jobs: [job.update(status="interrupted", ended_at=_now(),
                        stage="Bị ngắt khi TE Studio dừng") for job in jobs if job["status"] == "running"])
        except RuntimeError:
            pass  # Another live TE Studio process owns the worker.

    def _snapshot(self, state: StudioProject, identifier: str) -> tuple[Path, dict]:
        source = Path(state.directory).resolve()
        target = self.root / "snapshots" / identifier
        temporary = target.with_name(target.name + ".partial")
        if target.exists() or temporary.exists():
            raise FileExistsError(target)
        snapshot = deepcopy(state)
        try:
            with project_job_lock(source):
                StudioRenderService(self.studio).validate_inputs(snapshot, need_audio=False,
                                                                 need_render_tools=True)
                if snapshot.narration_mode == "multi":
                    speech_plan(snapshot)
                assets = _asset_fingerprints(snapshot)
                temporary.mkdir(parents=True)
                old_audio = source / "generated" / "audio"
                new_audio = target / "generated" / "audio"
                if old_audio.is_dir():
                    shutil.copytree(old_audio, temporary / "generated" / "audio")
                project_data = _rebase_audio(snapshot.project.to_dict(), old_audio, new_audio)
                snapshot.project = Project.from_dict(project_data)
                snapshot.directory = temporary
                snapshot.last_final = None
                _write_json(temporary / "project.json", project_data)
                timeline_path = source / snapshot.project.timeline_file
                timeline_data = json.loads(timeline_path.read_text(encoding="utf-8")) if timeline_path.is_file() \
                    else Timeline(project_id=snapshot.project.id).to_dict()
                _write_json(temporary / snapshot.project.timeline_file,
                            _rebase_audio(timeline_data, old_audio, new_audio))
                self.studio.save_project(snapshot)
                immutable_files = [temporary / "project.json", temporary / "studio.json",
                                   temporary / snapshot.project.timeline_file]
                audio_files = temporary / "generated" / "audio"
                if audio_files.is_dir():
                    immutable_files.extend(path for path in audio_files.rglob("*") if path.is_file())
                manifest = {"assets": assets, "files": {str(path.relative_to(temporary)): _hash_file(path)
                    for path in immutable_files}, "source_project": str(source)}
                _write_json(temporary / "snapshot-manifest.json", manifest)
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return target, manifest

    def enqueue(self, state: StudioProject) -> dict:
        identifier = uuid4().hex
        snapshot, manifest = self._snapshot(state, identifier)
        encoder = self.studio.settings.get("render_encoder", "auto")
        encoder = encoder if encoder in ("auto", "libx264", "h264_nvenc") else "auto"
        job = {"id": identifier, "project_id": state.project.id, "project_name": state.project.name,
               "project_path": str(Path(state.directory).resolve()), "render_mode": "full_hd",
               "snapshot_path": str(snapshot), "configuration": {"render_encoder": encoder,
                   "resolution": [1920, 1080], "fps": 30, "headline_seconds": state.headline_duration_seconds,
                   "output_root": str(Path(self.studio.settings["output_root"]).resolve()),
                   "source_fingerprint": _manifest_digest(manifest)},
               "created_at": _now(), "started_at": None, "ended_at": None, "status": "queued",
               "stage": "Đang chờ", "selected_encoder": encoder, "actual_encoder": None,
               "output_path": None, "performance_path": None, "error": None}
        self._change(lambda jobs: jobs.append(job))
        self._resume_if_enabled()
        return deepcopy(job)

    def _resume_if_enabled(self) -> None:
        if self._auto_continue and not self._pause.is_set():
            try:
                self.start()
            except RuntimeError as exc:
                self.last_error = str(exc)
                self.on_change()

    def _job(self, jobs: list[dict], identifier: str) -> dict:
        for job in jobs:
            if job["id"] == identifier:
                return job
        raise KeyError("Không tìm thấy tác vụ trong hàng đợi.")

    def remove_queued(self, identifier: str) -> None:
        def change(jobs):
            job = self._job(jobs, identifier)
            if job["status"] != "queued":
                raise ValueError("Chỉ có thể xóa tác vụ đang chờ.")
            jobs.remove(job)
            return job["snapshot_path"]
        path = self._change(change)
        shutil.rmtree(path, ignore_errors=True)

    def retry(self, identifier: str) -> None:
        def change(jobs):
            job = self._job(jobs, identifier)
            if job["status"] not in ("failed", "cancelled", "interrupted"):
                raise ValueError("Chỉ thử lại tác vụ lỗi, bị hủy hoặc gián đoạn.")
            job.update(status="queued", stage="Đang chờ thử lại", started_at=None, ended_at=None,
                       output_path=None, performance_path=None, error=None, actual_encoder=None)
            jobs.remove(job)
            jobs.append(job)
        self._change(change)
        self._resume_if_enabled()

    def update_from_project(self, identifier: str, state: StudioProject) -> None:
        old = next(job for job in self.jobs() if job["id"] == identifier)
        if old["status"] not in ("queued", "failed", "cancelled", "interrupted"):
            raise ValueError("Không thể cập nhật tác vụ đang chạy hoặc đã hoàn tất.")
        if old["project_id"] != state.project.id:
            raise ValueError("Tác vụ và project đang mở không khớp.")
        snapshot, manifest = self._snapshot(state, uuid4().hex)
        encoder = self.studio.settings.get("render_encoder", "auto")
        encoder = encoder if encoder in ("auto", "libx264", "h264_nvenc") else "auto"
        def change(jobs):
            job = self._job(jobs, identifier)
            if job["status"] not in ("queued", "failed", "cancelled", "interrupted"):
                raise ValueError("Tác vụ đã bắt đầu xử lý.")
            job.update(snapshot_path=str(snapshot), configuration={"render_encoder": encoder,
                "resolution": [1920, 1080], "fps": 30, "headline_seconds": state.headline_duration_seconds,
                "output_root": str(Path(self.studio.settings["output_root"]).resolve()),
                "source_fingerprint": _manifest_digest(manifest)}, status="queued", stage="Đã cập nhật snapshot",
                started_at=None, ended_at=None, output_path=None, performance_path=None,
                error=None, actual_encoder=None, selected_encoder=encoder)
        try:
            self._change(change)
        except Exception:
            shutil.rmtree(snapshot, ignore_errors=True)
            raise
        shutil.rmtree(old["snapshot_path"], ignore_errors=True)
        self._resume_if_enabled()

    def _validate_snapshot(self, job: dict, state: StudioProject) -> None:
        root = Path(job["snapshot_path"])
        manifest = json.loads((root / "snapshot-manifest.json").read_text(encoding="utf-8"))
        if _manifest_digest(manifest) != job["configuration"]["source_fingerprint"]:
            raise ValueError("Snapshot manifest đã thay đổi; hãy cập nhật tác vụ từ project.")
        for relative, digest in manifest["files"].items():
            if _hash_file(root / relative) != digest:
                raise ValueError(f"Snapshot đã thay đổi: {relative}. Hãy cập nhật tác vụ từ project.")
        if _asset_fingerprints(state) != manifest["assets"]:
            raise ValueError("Media hoặc font đã thay đổi sau khi xếp hàng; hãy cập nhật tác vụ từ project.")

    def _set(self, identifier: str, **fields) -> None:
        self._change(lambda jobs: self._job(jobs, identifier).update(**fields))

    @staticmethod
    def _working_copy(local: StudioService, source: StudioProject, destination: Path) -> StudioProject:
        shutil.copytree(source.directory, destination)
        old_audio = source.directory / "generated" / "audio"
        new_audio = destination / "generated" / "audio"
        project_data = _rebase_audio(source.project.to_dict(), old_audio, new_audio)
        _write_json(destination / "project.json", project_data)
        timeline_file = destination / source.project.timeline_file
        _write_json(timeline_file, _rebase_audio(json.loads(timeline_file.read_text(encoding="utf-8")),
                                                old_audio, new_audio))
        working = deepcopy(source)
        working.directory = destination
        working.project = Project.from_dict(project_data)
        local.save_project(working)
        return working

    def _run_job(self, job: dict) -> None:
        identifier = job["id"]
        snapshot = Path(job["snapshot_path"])
        local = StudioService(self.studio.config_path)
        local.settings["render_encoder"] = job["configuration"]["render_encoder"]
        local.settings["output_root"] = str(snapshot / "queued-output")
        renderer = None
        report_path = None
        working_path = None
        try:
            renderer = self.renderer_factory(local)
            self._renderer = renderer
            self._set(identifier, stage="Kiểm tra snapshot và media")
            state = local.open_project(snapshot)
            self._validate_snapshot(job, state)
            if self._cancel.is_set():
                raise QueueCancelled("Đã hủy trước khi render.")
            working_path = self.root / "runs" / identifier / uuid4().hex
            state = self._working_copy(local, state, working_path)
            local.settings["output_root"] = str(working_path / "queued-output")
            def progress(stage: str) -> None:
                if self._cancel.is_set():
                    raise QueueCancelled("Đã hủy tác vụ.")
                if stage.startswith("Bộ mã hóa xuất video: "):
                    self._set(identifier, stage=stage, actual_encoder=stage.split(": ", 1)[1])
                elif "thử lại bằng CPU libx264" in stage and "thất bại khi xuất" in stage:
                    self._set(identifier, stage=stage, actual_encoder="libx264")
                else:
                    self._set(identifier, stage=stage)
            while True:
                if self._cancel.is_set():
                    raise QueueCancelled("Đã hủy khi chờ bộ mã hóa.")
                try:
                    with project_job_lock(self.studio.config_path.parent / "render-execution"):
                        private_output = renderer.render_video(state, preview=False, progress=progress,
                                                               _machine_locked=True)
                    break
                except ValueError as exc:
                    if "đang tạo lời đọc hoặc render" not in str(exc):
                        raise
                    self._set(identifier, stage="Đang chờ render khác trên máy này")
                    self._cancel.wait(1)
            if self._cancel.is_set():
                raise QueueCancelled("Đã hủy sau khi render.")
            self._validate_assets_only(job, state)
            if self._cancel.is_set():
                raise QueueCancelled("Đã hủy trước khi công bố MP4.")
            self._set(identifier, stage="Đang công bố MP4")
            output = StudioRenderService._publish(private_output,
                Path(job["configuration"]["output_root"]), job["project_name"])
            report_path = self._latest_report(working_path)
            observed = next((item["actual_encoder"] for item in self.jobs() if item["id"] == identifier), None)
            actual = self._actual_encoder(report_path) or observed
            self._set(identifier, status="completed", stage="Hoàn tất", ended_at=_now(),
                output_path=str(output), performance_path=str(report_path) if report_path else None,
                actual_encoder=actual)
        except Exception as exc:
            report_path = report_path or (self._latest_report(working_path) if working_path else None)
            cancelled = self._cancel.is_set() or isinstance(exc, QueueCancelled)
            observed = next((item["actual_encoder"] for item in self.jobs() if item["id"] == identifier), None)
            self._set(identifier, status="cancelled" if cancelled else "failed", ended_at=_now(),
                stage="Đã hủy" if cancelled else "Lỗi", error=str(exc),
                performance_path=str(report_path) if report_path else None,
                actual_encoder=self._actual_encoder(report_path) or observed)
        finally:
            self._renderer = None
            self._cancel.clear()

    def _validate_assets_only(self, job: dict, state: StudioProject) -> None:
        manifest = json.loads((Path(job["snapshot_path"]) / "snapshot-manifest.json").read_text(encoding="utf-8"))
        if _manifest_digest(manifest) != job["configuration"]["source_fingerprint"]:
            raise ValueError("Snapshot manifest thay đổi trong khi render; MP4 chưa được công bố.")
        if _asset_fingerprints(state) != manifest["assets"]:
            raise ValueError("Media hoặc font thay đổi trong khi render; MP4 chưa được công bố.")

    @staticmethod
    def _latest_report(snapshot: Path) -> Path | None:
        folder = snapshot / "generated" / "output" / "diagnostics"
        return next(iter(sorted(folder.glob("performance-*-full.json"), reverse=True)), None)

    @staticmethod
    def _actual_encoder(path: Path | None) -> str | None:
        if path and path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("encoder")
            except (OSError, ValueError):
                pass
        return None

    def _work(self) -> None:
        try:
            with _file_lock(self.worker_lock, blocking=False):
                while not self._pause.is_set():
                    def take(jobs):
                        job = next((item for item in jobs if item["status"] == "queued"), None)
                        if job:
                            job.update(status="running", started_at=_now(), ended_at=None,
                                       stage="Đang chuẩn bị", error=None)
                        return deepcopy(job) if job else None
                    job = self._change(take)
                    if not job:
                        break
                    self._run_job(job)
        except Exception as exc:
            self.last_error = str(exc)
            self._auto_continue = False
        finally:
            with self._local:
                self._thread = None
            self.on_change()
            if self._auto_continue and not self._pause.is_set() and any(
                    job["status"] == "queued" for job in self.jobs()):
                self._resume_if_enabled()

    def start(self) -> None:
        with self._local:
            if self._thread and self._thread.is_alive():
                self._auto_continue = True
                self._pause.clear()
                return
            with _file_lock(self.worker_lock, blocking=False):
                pass
            self.last_error = None
            self._auto_continue = True
            self._pause.clear()
            self._thread = Thread(target=self._work, name="te-render-queue", daemon=True)
            self._thread.start()

    def pause_after_current(self) -> None:
        self._auto_continue = False
        self._pause.set()
        self.on_change()

    def cancel_current(self) -> None:
        if not any(job["status"] == "running" for job in self.jobs()):
            return
        self._cancel.set()
        if self._renderer:
            self._renderer.render.cancel_render()

    def is_active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
