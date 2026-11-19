"""One-shot local handoff for an explicitly prepared Studio MP4.

The Native Host serves only the path recorded in a prepared Publisher job. It does
not expose a general filesystem read command or send MP4 bytes to Chrome.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from uuid import uuid4

from te.project.manager import _write_json
from .channel_probe import channel_id_from_url
from .publisher import PublisherStore, file_identity
from .studio_connector import StudioConnectorStore, _studio_url


class StudioUploadBridge:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.path = self.config_path.parent / "studio-connector" / "upload-request.json"
        self.lock_path = self.path.with_name("upload-active.lock")

    def _read(self) -> dict | None:
        if not self.path.exists():
            return None
        request = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or request.get("schema") != 1:
            raise ValueError("Yêu cầu upload Connector không hợp lệ.")
        return request

    def retryable_failure(self, job: dict) -> bool:
        request = self._read()
        if not request or request.get("job_id") != job.get("id") or \
                request.get("attempt_id") != job.get("attempt_id") or \
                request.get("status") != "failed" or request.get("file_selection_started"):
            return False
        if self.lock_path.exists():
            if self.lock_path.read_text(encoding="utf-8") != request["attempt_id"]:
                return False
            self.lock_path.unlink()  # Only this completed pre-upload attempt owns the lock.
        return True

    def queue(self, job_id: str, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        existing = self._read()
        if existing and existing.get("status") in {"waiting_for_browser", "claimed", "started", "file_selected"}:
            raise ValueError("Đã có yêu cầu Connector đang chờ hoặc chưa rõ kết quả; không gửi lại.")
        if existing and existing.get("status") == "failed" and not existing.get("file_selection_started") and \
                self.lock_path.exists() and \
                self.lock_path.read_text(encoding="utf-8") == existing.get("attempt_id"):
            self.lock_path.unlink()
        store = PublisherStore(self.config_path, recover_studio=False)
        job = next((item for item in store.data["jobs"] if item.get("id") == job_id), None)
        if not job or job.get("status") != "prepared" or job.get("visibility") != "private" or \
                not job.get("fingerprint", "").startswith("studio-private:"):
            raise ValueError("Chỉ tác vụ Studio PRIVATE đã chuẩn bị mới được gửi tới Chrome.")
        record = next((item for item in StudioConnectorStore(self.config_path).load()["channels"]
                       if item.get("key") == job["channel_key"]), None)
        try:
            verified_at = datetime.fromisoformat(record["identity_verified_at"])
        except (KeyError, TypeError, ValueError):
            verified_at = None
        if not record or record.get("id") != job["channel_id"] or \
                record.get("id_source") != "studio_url_user_confirmed" or \
                record.get("status") == "unavailable" or not verified_at or \
                verified_at.tzinfo is None or not timedelta(0) <= now - verified_at <= timedelta(hours=24):
            raise ValueError("Danh tính kênh Studio đã đổi hoặc hết hạn; xác minh lại trước khi upload.")
        request = {"schema": 1, "request_id": uuid4().hex, "attempt_id": uuid4().hex,
                   "job_id": job_id, "channel_id": job["channel_id"], "status": "waiting_for_browser",
                   "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat()}
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock_path.open("x", encoding="utf-8") as lock:
                lock.write(request["attempt_id"])
        except FileExistsError as exc:
            raise ValueError("Đã có lần thử Connector trên máy này; kiểm tra Studio trước khi tạo lần khác.") from exc
        try:
            store.update(job_id, status="waiting_for_browser", attempt_id=request["attempt_id"],
                         attempt_started_at=now.isoformat(), last_confirmed_operation="queued_for_connector")
            _write_json(self.path, request)
        except Exception:
            self.lock_path.unlink(missing_ok=True)
            raise
        return request

    def pending(self, *, now: datetime | None = None) -> dict:
        request = self._read()
        if not request or request.get("status") != "waiting_for_browser":
            return {"pending": False}
        now = now or datetime.now(timezone.utc)
        if now >= datetime.fromisoformat(request["expires_at"]):
            self.fail(request["request_id"], "Yêu cầu hết hạn trước khi Chrome nhận tệp.")
            return {"pending": False}
        return {"pending": True, "request_id": request["request_id"],
                "channel_id": request["channel_id"], "expires_at": request["expires_at"]}

    def claim(self, request_id: str, page_url: str, page_url_after: str) -> dict:
        request = self._read()
        if not request or request.get("request_id") != request_id or \
                request.get("status") != "waiting_for_browser" or not self.pending().get("pending"):
            raise ValueError("Yêu cầu upload đã dùng hoặc hết hạn.")
        if not _studio_url(page_url) or page_url != page_url_after or \
                channel_id_from_url(page_url) != request["channel_id"]:
            raise ValueError("Channel ID của tab Studio không khớp đích upload.")
        store = PublisherStore(self.config_path, recover_studio=False)
        job = next((item for item in store.data["jobs"] if item.get("id") == request["job_id"]), None)
        if not job or job.get("status") != "waiting_for_browser" or \
                job.get("attempt_id") != request["attempt_id"] or job["channel_id"] != request["channel_id"]:
            raise ValueError("Tác vụ TE không còn khớp yêu cầu upload.")
        actual = file_identity(Path(job["source"]["path"]))
        if actual["sha256"] != job["source"]["sha256"] or actual["bytes"] != job["source"]["bytes"]:
            raise ValueError("MP4 đã thay đổi sau khi chuẩn bị; upload bị chặn.")
        record = next((item for item in StudioConnectorStore(self.config_path).load()["channels"]
                       if item.get("key") == job["channel_key"]), None)
        if not record or record.get("id") != job["channel_id"] or \
                record.get("id_source") != "studio_url_user_confirmed":
            raise ValueError("Danh tính kênh đã đổi sau khi gửi yêu cầu.")
        store.update(job["id"], status="channel_verified", last_confirmed_operation="studio_url_matched")
        _write_json(self.path, dict(request, status="claimed"))
        return {"request_id": request_id, "attempt_id": request["attempt_id"],
                "channel_id": request["channel_id"], "path": actual["path"], "bytes": actual["bytes"],
                "filename": Path(actual["path"]).name}

    def started(self, request_id: str) -> None:
        request = self._read()
        if not request or request.get("request_id") != request_id or request.get("status") != "claimed":
            raise ValueError("Yêu cầu chưa xác minh kênh hoặc đã bắt đầu.")
        PublisherStore(self.config_path, recover_studio=False).update(request["job_id"],
            status="uploading", last_confirmed_operation="file_selection_started")
        _write_json(self.path, dict(request, status="started", file_selection_started=True))

    def file_selected(self, request_id: str, filename: str, size: int) -> None:
        request = self._read()
        if not request or request.get("request_id") != request_id or request.get("status") != "started":
            raise ValueError("Yêu cầu chọn file không hợp lệ.")
        store = PublisherStore(self.config_path, recover_studio=False)
        job = next(item for item in store.data["jobs"] if item["id"] == request["job_id"])
        if filename != Path(job["source"]["path"]).name or size != job["source"]["bytes"]:
            self.fail(request_id, "Chrome nhận tệp khác MP4 đã chọn; kiểm tra Studio.")
            raise ValueError("Chrome nhận sai tên hoặc kích thước MP4.")
        store.update(job["id"], status="uploading", last_confirmed_operation="browser_file_input_received")
        _write_json(self.path, dict(request, status="file_selected"))

    def fail(self, request_id: str, reason: str, diagnostics: dict | None = None) -> None:
        request = self._read()
        if not request or request.get("request_id") != request_id or request.get("status") == "failed":
            return
        uncertain = request["status"] in {"started", "file_selected"}
        messages = {"missing_input": "YouTube Studio đã mở nhưng chưa tìm được ô chọn video. Kiểm tra giao diện upload hoặc thử lại sau khi khắc phục.",
                    "debugger_failure": "Chrome không cho Connector đọc giao diện upload; kiểm tra quyền debugger hoặc extension khác đang dùng debugger.",
                    "channel_mismatch": "Channel ID của tab Studio không khớp kênh đã chọn trong TE.",
                    "chrome_disconnected": "Chrome ngắt kết nối khi xử lý tệp; kiểm tra Studio trước khi thử lại."}
        detail = messages.get(reason, str(reason))
        diagnostics = diagnostics or {}
        summary = (f" Bước: {diagnostics.get('browser_step', 'chưa rõ')}; "
                   f"hộp upload: {'đã mở' if diagnostics.get('dialog_open') else 'chưa xác nhận'}; "
                   f"input: {diagnostics.get('candidate_count', 0)}; "
                   f"loại: {', '.join(diagnostics.get('rejected', [])) or 'không có'}; "
                   f"CDP: {'đã gắn' if diagnostics.get('cdp_attached') else 'chưa gắn'}; "
                   f"chọn tệp: {'đã bắt đầu' if uncertain else 'chưa bắt đầu'}.")
        PublisherStore(self.config_path, recover_studio=False).update(request["job_id"],
            status="needs_attention" if uncertain else "failed_before_upload",
            error=(detail + summary)[:1000], browser_diagnostics=diagnostics,
            last_confirmed_operation="browser_failure")
        _write_json(self.path, dict(request, status="failed", failure_reason=reason,
                                    file_selection_started=uncertain))
        if not uncertain:
            self.lock_path.unlink(missing_ok=True)
