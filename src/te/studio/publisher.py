"""Machine-local, crash-safe records for explicitly initiated Studio test uploads."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from uuid import uuid4

from te.project.manager import _write_json

CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MODES = {"immediate", "scheduled"}
STATUSES = {"draft", "ready", "uploading", "processing", "configuring",
            "scheduled", "published", "failed", "needs_attention", "private_test"}
DEFAULTS = {"channel_id": "", "video_path": "", "thumbnail_path": "", "title": "",
            "description": "", "tags": "", "playlist": "", "audience": "not_made_for_kids",
            "mode": "immediate", "scheduled_local": "", "timezone": "Asia/Tokyo",
            "monetization": "requested", "end_screen": "most_recent_upload",
            "status": "draft", "video_id": "", "video_url": ""}


def normalize_metadata(raw: object) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    result = {key: raw.get(key, value) for key, value in DEFAULTS.items()}
    for key in DEFAULTS:
        if not isinstance(result[key], str):
            result[key] = DEFAULTS[key]
    if result["mode"] not in MODES:
        result["mode"] = "immediate"
    if result["audience"] not in {"made_for_kids", "not_made_for_kids"}:
        result["audience"] = "not_made_for_kids"
    if result["status"] not in STATUSES:
        result["status"] = "draft"
    if result["timezone"] != "Asia/Tokyo":
        result["timezone"] = "Asia/Tokyo"
    return result


def validate_schedule(value: str, timezone: str) -> str:
    if timezone != "Asia/Tokyo":
        raise ValueError("Múi giờ xuất bản phải là Asia/Tokyo.")
    try:
        local = datetime.fromisoformat(value)
        if local.tzinfo is not None:
            raise ValueError
        aware = local.replace(tzinfo=ZoneInfo(timezone))
        if aware <= datetime.now(ZoneInfo(timezone)):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Thời điểm hẹn giờ phải là ngày giờ tương lai theo Asia/Tokyo, dạng YYYY-MM-DD HH:MM.") from exc
    return aware.isoformat()


def file_identity(path: Path) -> dict:
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != ".mp4" or not path.is_file():
        raise ValueError("Hãy chọn video Full HD MP4 đã hoàn tất.")
    stat = path.stat()
    if not stat.st_size:
        raise ValueError("Video MP4 trống.")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": stat.st_size, "sha256": digest.hexdigest()}


class PublisherStore:
    """One local store per installation. Jobs survive project switches and restarts."""

    def __init__(self, config_path: Path):
        self.root = Path(config_path).parent / "youtube-publisher"
        self.path = self.root / "jobs.json"
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("jobs"), list):
                raise ValueError("Hồ sơ tác vụ YouTube không tương thích.")
        else:
            data = {"version": 1, "jobs": [], "channels": []}
        self.data = data
        self.data.setdefault("channels", [])
        recovered = False
        for job in self.data["jobs"]:
            if job.get("status") in {"uploading", "processing", "configuring"}:
                job["status"] = "needs_attention"
                job["error"] = "TE Studio dừng khi tác vụ chưa xác nhận; kiểm tra video trong YouTube Studio trước khi thử lại."
                recovered = True
        if recovered:
            self._save()

    def _save(self) -> None:
        _write_json(self.path, self.data)

    def channels(self) -> list[dict]:
        return deepcopy(self.data["channels"])

    def add_verified_channel(self, channel_id: str, name: str) -> None:
        if not CHANNEL_ID.fullmatch(channel_id) or not name.strip():
            raise ValueError("ID hoặc tên kênh không hợp lệ.")
        channels = [c for c in self.data["channels"] if c["id"] != channel_id]
        channels.append({"id": channel_id, "name": name.strip(),
                         "verified_at": datetime.now().astimezone().isoformat()})
        self.data["channels"] = channels
        self._save()

    def prepare(self, project_id: str, metadata: dict, identity: dict) -> dict:
        meta = normalize_metadata(metadata)
        channel = meta["channel_id"]
        if not CHANNEL_ID.fullmatch(channel):
            raise ValueError("Chọn một kênh Studio đã xác minh.")
        if channel not in {c["id"] for c in self.data["channels"]}:
            raise ValueError("Kênh chưa được xác minh trong phiên Studio này.")
        if not meta["title"].strip() or len(meta["title"]) > 100:
            raise ValueError("Tiêu đề YouTube phải có 1–100 ký tự.")
        if meta["mode"] == "scheduled":
            validate_schedule(meta["scheduled_local"], meta["timezone"])
        key = sha256(json.dumps([project_id, channel, identity["sha256"], meta["mode"],
            meta["scheduled_local"]], ensure_ascii=False).encode("utf-8")).hexdigest()
        prior = next((job for job in self.data["jobs"] if job["fingerprint"] == key), None)
        if prior:
            return deepcopy(prior)
        job = {"id": uuid4().hex, "fingerprint": key, "project_id": project_id,
               "channel_id": channel, "source": deepcopy(identity), "metadata": meta,
               "status": "ready", "video_id": "", "video_url": "", "error": "",
               "last_confirmed_operation": "source_validated",
               "created_at": datetime.now().astimezone().isoformat()}
        self.data["jobs"].append(job)
        self._save()
        return deepcopy(job)

    def begin_attempt(self, job_id: str) -> dict:
        """Reserve the upload fingerprint across TE Studio processes before browser I/O.

        The reservation intentionally survives a crash: an uncertain upload must be
        inspected in Studio, never started again automatically.
        """
        job = next((j for j in self.data["jobs"] if j["id"] == job_id), None)
        if job is None or job["status"] != "ready":
            raise ValueError("Tác vụ không còn sẵn sàng để tải lên.")
        lock_dir = self.root / "attempts"
        lock_dir.mkdir(parents=True, exist_ok=True)
        reservation = lock_dir / (job["fingerprint"] + ".lock")
        try:
            with reservation.open("x", encoding="utf-8") as stream:
                stream.write(job_id + "\n")
        except FileExistsError as exc:
            raise ValueError("Video đã được một phiên TE Studio khác bắt đầu tải; kiểm tra YouTube Studio trước khi thử lại.") from exc
        return self.update(job_id, status="uploading", last_confirmed_operation="attempt_started")

    def update(self, job_id: str, **changes) -> dict:
        job = next((j for j in self.data["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise ValueError("Không tìm thấy tác vụ xuất bản.")
        if "status" in changes and changes["status"] not in STATUSES:
            raise ValueError("Trạng thái tác vụ không hợp lệ.")
        if "video_id" in changes and changes["video_id"] and not VIDEO_ID.fullmatch(changes["video_id"]):
            raise ValueError("YouTube video ID không hợp lệ.")
        job.update(changes)
        self._save()
        return deepcopy(job)

    def latest(self, project_id: str) -> dict | None:
        return next((deepcopy(j) for j in reversed(self.data["jobs"])
                     if j["project_id"] == project_id), None)
