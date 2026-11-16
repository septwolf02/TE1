"""Compact publisher dialog; all browser and file hashing work stays off Tk's thread."""
from __future__ import annotations

from pathlib import Path
import json
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .publisher import PublisherStore, file_identity, normalize_metadata
from .studio_connector import StudioConnectorStore
from .youtube_api import (CredentialVault, OwnerOAuth, YouTubeAPIClient,
                          YouTubeAPIError, validate_client_config)
from te.project.manager import _write_json


class PublisherController:
    def __init__(self, app):
        self.app = app
        self.store = PublisherStore(app.service.config_path)
        self.studio_store = StudioConnectorStore(app.service.config_path)
        self.events = Queue()
        self.busy = False
        self.values = {key: tk.StringVar() for key in normalize_metadata({})}
        self.status = tk.StringVar(value="Upload tự động tạm khóa: TE chưa xác minh tài khoản và quyền kênh.")
        self.channel_name = tk.StringVar()
        self.oauth_status = tk.StringVar(value="Owner OAuth: chưa kết nối kênh API trên máy này.")
        self.studio_status = tk.StringVar(value="Studio Connector: chưa nhận bản quét từ extension.")
        app.root.after(300, self.poll)

    def build(self):
        host = self.app.body
        canvas = tk.Canvas(host, highlightthickness=0)
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.app.body = content
        box = self.app._group("7. Xuất bản YouTube")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Owner OAuth qua trình duyệt hệ thống. Chỉ kênh được YouTube API xác nhận mới dùng được tải thử Riêng tư; quyền Editor qua Studio không đủ cho API.",
                  wraplength=850).grid(row=0, column=0, columnspan=3, sticky="w")
        for column, (label, command) in enumerate((("Chọn OAuth JSON (Owner)", self.configure_oauth),
            ("Ủy quyền thêm kênh", self.authorize_owner), ("Kiểm tra kết nối", self.check_connections))):
            ttk.Button(box, text=label, command=command).grid(row=1, column=column, sticky="w", padx=4, pady=4)
        ttk.Button(box, text="Thu hồi quyền kênh", command=self.revoke_owner).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Button(box, text="Xóa kết nối cục bộ", command=self.remove_local_connection).grid(
            row=1, column=4, sticky="w", padx=4)
        ttk.Label(box, textvariable=self.status, wraplength=850).grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(box, textvariable=self.oauth_status, wraplength=850).grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Button(box, text="Quét kênh Studio", command=self.scan_studio).grid(row=2, column=4, sticky="w", padx=4)
        ttk.Label(box, textvariable=self.studio_status, wraplength=330).grid(row=3, column=4, sticky="w")
        ttk.Label(box, text="Channel ID (API/Studio)").grid(row=4, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["channel_id"], width=35, state="readonly").grid(row=4, column=1, sticky="ew")
        ttk.Label(box, text="Tên kênh").grid(row=5, column=0, sticky="w")
        ttk.Label(box, textvariable=self.channel_name).grid(row=5, column=1, sticky="w")
        ttk.Label(box, text="Chọn kênh").grid(row=6, column=0, sticky="w")
        self.channel_combo = ttk.Combobox(box, state="readonly")
        self.channel_combo.grid(row=6, column=1, sticky="ew")
        self.channel_combo.bind("<<ComboboxSelected>>", self._select_channel)
        row = 7
        for key, label in (("video_path", "Video Full HD MP4"), ("thumbnail_path", "Thumbnail")):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(box, textvariable=self.values[key]).grid(row=row, column=1, sticky="ew")
            ttk.Button(box, text="Chọn…", command=lambda k=key: self.choose(k)).grid(row=row, column=2)
            row += 1
        for key, label in (("title", "Tiêu đề tiếng Nhật"), ("description", "Mô tả tiếng Nhật"),
                           ("tags", "Tags"), ("playlist", "Playlist")):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w")
            if key == "description":
                self.description = tk.Text(box, height=4, wrap="word")
                self.description.grid(row=row, column=1, sticky="ew")
            else:
                ttk.Entry(box, textvariable=self.values[key]).grid(row=row, column=1, sticky="ew")
            row += 1
        for key, label, choices in (("audience", "Đối tượng", ("not_made_for_kids", "made_for_kids")),
            ("mode", "Chế độ dự kiến", ("immediate", "scheduled")),
            ("monetization", "Kiếm tiền dự kiến", ("requested", "off")),
            ("end_screen", "End screen dự kiến", ("most_recent_upload", "off"))):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w")
            ttk.Combobox(box, textvariable=self.values[key], values=choices, state="readonly").grid(
                row=row, column=1, sticky="ew")
            row += 1
        ttk.Label(box, text="Hẹn giờ (YYYY-MM-DD HH:MM)").grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["scheduled_local"]).grid(row=row, column=1, sticky="ew")
        ttk.Label(box, text="Asia/Tokyo").grid(row=row, column=2)
        row += 1
        ttk.Label(box, text="Tải thử Owner chỉ đăng Riêng tư: áp dụng title, description, tags, audience và thumbnail. Đăng công khai, hẹn giờ, playlist, kiếm tiền và end screen chưa tự động áp dụng.",
                  wraplength=850).grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        row += 1
        ttk.Button(box, text="Lưu thông tin xuất bản", command=self.save).grid(row=row, column=0, sticky="w")
        self.upload_button = ttk.Button(box, text="Tải thử Riêng tư (Owner)", command=self.upload, state="disabled")
        self.upload_button.grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(box, textvariable=self.values["status"]).grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["video_id"], state="readonly", width=16).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(box, text="YouTube URL").grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["video_url"], state="readonly").grid(row=row, column=1, sticky="ew")
        self.refresh_channels()
        self.app.body = host

    def refresh_channels(self):
        channels = [dict(channel, connection_type="youtube_api") for channel in self.store.channels()]
        try:
            scan = self.studio_store.load()
            channels.extend(scan["channels"])
            if scan["scanned_at"]:
                verified = sum(c["status"] == "identity_verified" for c in scan["channels"])
                manual = sum(c["status"] == "identity_user_named" for c in scan["channels"])
                self.studio_status.set(f"Studio Connector: {scan['scanned_at']} • {verified} ID có chứng cứ • "
                                       f"{manual} tên nhập tay • {len(scan['channels'])} bản ghi • " +
                                       ("đã cũ" if scan.get("stale") else "quyền upload chưa xác minh"))
        except (OSError, ValueError, KeyError, TypeError):
            self.studio_status.set("Studio Connector: bản quét không đọc được; hãy quét lại trong extension.")
        self.channel_records = channels
        self.channel_combo["values"] = [
            f"{c['name'] or '(chưa có tên)'} — {c['id'] or '(chưa có ID)'} — " +
            ("YouTube API: OAuth OK" if c.get("authorization_status") == "authorized" else
             "YouTube API: chưa ủy quyền" if c["connection_type"] == "youtube_api" else
             "Studio Connector: " + c["status"] +
             (" • tên sửa tay" if c.get("name_source") == "user_edited" else ""))
            for c in channels]
        self._refresh_upload_button()

    def _selected_connection(self):
        if self.values["connection_type"].get() != "youtube_api":
            return None
        return next((c for c in self.store.authorized_channels()
                     if c["id"] == self.values["channel_id"].get()), None)

    def _refresh_upload_button(self):
        if hasattr(self, "upload_button"):
            self.upload_button.configure(state="normal" if self._selected_connection() else "disabled")

    def _select_channel(self, _event=None):
        index = self.channel_combo.current()
        channels = self.channel_records
        if 0 <= index < len(channels):
            selected = channels[index]
            if selected["connection_type"] == "studio_connector" and selected["status"] not in \
                    {"identity_verified", "identity_user_named", "identity_user_confirmed"} or \
                    selected["connection_type"] == "studio_connector" and not selected["name"]:
                self.status.set("Kênh Studio chưa có ID được xác minh hoặc bản quét đã cũ; không thể chọn làm đích.")
                return
            self.values["channel_id"].set(selected["id"])
            self.values["connection_type"].set(selected["connection_type"])
            self.channel_name.set(selected["name"])
            self.oauth_status.set("Studio Connector: quyền upload chưa xác minh" if
                selected["connection_type"] == "studio_connector" else
                "API authorization: " + selected["authorization_status"])
            self._refresh_upload_button()

    def scan_studio(self):
        """Refresh the latest user-triggered Chrome scan; TE never drives Chrome."""
        self.refresh_channels()
        self.status.set("Mở Studio trong Chrome, mở menu chuyển kênh, bấm Quét trong extension, rồi bấm nút này để cập nhật danh sách. Không xác minh quyền upload.")

    def choose(self, key):
        path = filedialog.askopenfilename(parent=self.app._details["publisher"],
            filetypes=[("MP4", "*.mp4")] if key == "video_path" else [("JPEG/PNG", "*.png *.jpg *.jpeg")])
        if path:
            self.values[key].set(path)

    def load(self, state):
        meta = normalize_metadata(state.publisher)
        if not meta["video_path"]:
            meta["video_path"] = self.default_video(state)
        for key, value in meta.items():
            self.values[key].set(value)
        self.description.delete("1.0", "end")
        self.description.insert("1.0", meta["description"])
        self.refresh_channels()
        selected = next((c for c in self.channel_records if c["id"] == meta["channel_id"] and
                         c["connection_type"] == meta["connection_type"]), None)
        if selected:
            self.channel_combo.current(self.channel_records.index(selected))
        else:
            self.channel_combo.set("")
        self.channel_name.set(selected["name"] if selected else "")
        self.oauth_status.set("Studio Connector: quyền upload chưa xác minh" if selected and
            selected["connection_type"] == "studio_connector" else
            "API authorization: " + (selected["authorization_status"] if selected else "chưa kết nối"))
        self._refresh_upload_button()
        self.status.set("Kênh có OAuth API authorization; có thể tải thử Riêng tư trên máy Owner."
                        if self._selected_connection() else "Kênh chưa có OAuth API authorization; upload đã bị chặn.")
        job = self.store.latest(state.project.id)
        if job:
            self.values["status"].set(job["status"])
            if job["status"] == "needs_attention":
                self.status.set("Cần kiểm tra tác vụ trong YouTube Studio trước khi thử lại: " + job.get("error", ""))
            self.values["video_id"].set(job.get("video_id", ""))
            self.values["video_url"].set(job.get("video_url", ""))

    def default_video(self, state):
        completed = [job for job in self.app.queue.jobs() if job["project_id"] == state.project.id
                     and job["status"] == "completed" and job.get("output_path")]
        return completed[-1]["output_path"] if completed else (state.last_final or "")

    def suggest_video(self):
        if self.app.state and not self.values["video_path"].get():
            self.values["video_path"].set(self.default_video(self.app.state))

    def capture(self):
        self.values["description"].set(self.description.get("1.0", "end-1c"))
        if self.app.state:
            self.app.state.publisher = normalize_metadata({key: var.get() for key, var in self.values.items()})

    def save(self):
        self.capture()
        self.app._save()

    def _start(self, action, worker):
        if self.busy:
            self.status.set("Đang xử lý yêu cầu YouTube; vui lòng chờ.")
            return
        self.busy = True
        self.status.set("Đang xử lý YouTube OAuth/API trong nền…")
        def run():
            try:
                result = worker()
                self.events.put((action, result, None))
            except Exception as exc:
                self.events.put((action, None, exc))
        Thread(target=run, daemon=True).start()

    def configure_oauth(self):
        path = filedialog.askopenfilename(parent=self.app._details["publisher"],
                                          filetypes=[("OAuth Desktop JSON", "*.json")])
        if not path:
            return
        try:
            config = validate_client_config(json.loads(Path(path).read_text(encoding="utf-8")))
            target = self.store.root / "oauth-desktop-client.json"
            _write_json(target, config)
            self.app.service.settings["youtube_oauth_client_path"] = str(target)
            self.app.service.save_settings()
        except Exception as exc:
            messagebox.showerror("YouTube OAuth", f"Không lưu được cấu hình OAuth Desktop: {exc}", parent=self.app.root)
            return
        self.oauth_status.set("Đã lưu OAuth Desktop client trong cấu hình máy; chưa có token/kênh được cấp quyền.")

    def _owner(self):
        path = self.app.service.settings.get("youtube_oauth_client_path")
        if not path:
            raise YouTubeAPIError("Chọn OAuth Desktop JSON trong phần Owner trước.")
        vault = CredentialVault()
        return OwnerOAuth(Path(path), vault, YouTubeAPIClient())

    def authorize_owner(self):
        if not messagebox.askyesno("Owner OAuth", "Chỉ thực hiện trên máy của chủ kênh. Google sẽ mở trình duyệt hệ thống để cấp youtube.upload và youtube.readonly. Tiếp tục?",
                                   parent=self.app._details["publisher"]):
            return
        def work():
            record = self._owner().authorize()
            try:
                self.store.register_authorized_channel(record)
            except Exception:
                CredentialVault().delete(record["credential_ref"])
                raise
            return record
        self._start("authorized", work)

    def check_connections(self):
        records = self.store.authorized_channels()
        def work():
            owner = self._owner()
            results = []
            for record in records:
                credentials = owner.credentials_for(record["credential_ref"])
                actual = owner.api.verify_exact_channel(credentials, record["id"])
                self.store.mark_checked(actual["id"], actual["name"], actual["thumbnail_url"])
                results.append(actual["id"])
            return results
        self._start("checked", work)

    def revoke_owner(self):
        record = self._selected_connection()
        if not record:
            self.status.set("Chọn kênh có OAuth authorization trước.")
            return
        if not messagebox.askyesno("Thu hồi OAuth", f"Thu hồi quyền API của kênh {record['name']} ({record['id']})? Tác vụ cũ vẫn được giữ.",
                                   parent=self.app._details["publisher"]):
            return
        def work():
            self._owner().revoke(record["credential_ref"])
            self.store.revoke_channel(record["id"])
            return record["id"]
        self._start("revoked", work)

    def remove_local_connection(self):
        record = self._selected_connection()
        if not record:
            self.status.set("Chọn kênh có OAuth authorization trước.")
            return
        if not messagebox.askyesno("Xóa kết nối cục bộ", "Xóa credential khỏi máy này? Quyền Google OAuth có thể vẫn còn; thu hồi trong Google Account nếu cần.",
                                   parent=self.app._details["publisher"]):
            return
        def work():
            CredentialVault().delete(record["credential_ref"])
            self.store.revoke_channel(record["id"])
            return record["id"]
        self._start("revoked_local", work)

    def upload(self):
        record = self._selected_connection()
        state = self.app.state
        if not record or not state:
            self.status.set("Kênh chưa có OAuth authorization hợp lệ; upload đã bị chặn.")
            return
        self.capture()
        if not self.app._save():
            return
        meta = normalize_metadata(state.publisher)
        if meta["mode"] != "immediate":
            self.status.set("Phase 4A-API chỉ tải thử Riêng tư; lịch hẹn chưa được áp dụng.")
            return
        if not messagebox.askyesno("Tải thử Riêng tư — Owner", f"Chủ kênh xác nhận tải MP4 lên {record['name']} ({record['id']}) ở chế độ PRIVATE?\nĐối tượng: {meta['audience']}.\n\nKhông dùng trên máy nhân viên.",
                                   parent=self.app._details["publisher"]):
            return
        project_id = state.project.id
        def work():
            owner = self._owner()
            credentials = owner.credentials_for(record["credential_ref"])
            owner.api.verify_exact_channel(credentials, record["id"])
            identity = file_identity(Path(meta["video_path"]))
            job = self.store.prepare(project_id, meta, identity)
            if job["status"] != "ready" or job.get("credential_ref") != record["credential_ref"]:
                raise YouTubeAPIError("Tác vụ đã tồn tại hoặc OAuth của kênh đã đổi; kiểm tra YouTube trước khi thử lại.")
            self.store.begin_attempt(job["id"])
            try:
                result = owner.api.upload_private(credentials, record["id"], Path(identity["path"]), meta,
                    on_created=lambda video_id: self.store.update(job["id"], status="processing", video_id=video_id,
                        video_url=f"https://www.youtube.com/watch?v={video_id}",
                        last_confirmed_operation="video_created"),
                    thumbnail=Path(meta["thumbnail_path"]) if meta["thumbnail_path"] else None)
            except Exception as exc:
                self.store.update(job["id"], status="needs_attention", error=str(exc))
                raise
            status = "needs_attention" if result["thumbnail_error"] else "private_test"
            return self.store.update(job["id"], status=status, video_id=result["video_id"],
                video_url=result["video_url"], error=result["thumbnail_error"],
                last_confirmed_operation="private_video_created")
        self._start("upload", work)

    def poll(self):
        while True:
            try:
                action, result, error = self.events.get_nowait()
            except Empty:
                break
            self.busy = False
            if error:
                self.status.set(f"{action}: {error}")
                messagebox.showerror("YouTube", str(error), parent=self.app.root)
            elif action == "authorized":
                self.refresh_channels()
                self.values["channel_id"].set(result["id"])
                self.values["connection_type"].set("youtube_api")
                self.channel_combo.current(next(i for i, channel in enumerate(self.channel_records)
                                                if channel["id"] == result["id"] and
                                                channel["connection_type"] == "youtube_api"))
                self.channel_name.set(result["name"])
                self._refresh_upload_button()
                self.oauth_status.set(f"OAuth OK: {result['name']} — {result['id']}")
                self.status.set("Kênh đã được YouTube API xác nhận; chưa upload video.")
            elif action == "checked":
                self.refresh_channels()
                self.status.set(f"YouTube API xác nhận {len(result)} kênh đã đăng ký.")
            elif action == "revoked":
                self.refresh_channels()
                self.oauth_status.set("API authorization: revoked")
                self.status.set(f"Đã thu hồi OAuth của kênh {result}.")
            elif action == "revoked_local":
                self.refresh_channels()
                self.oauth_status.set("API authorization: disconnected_local")
                self.status.set(f"Đã xóa credential cục bộ của kênh {result}; Google grant có thể vẫn còn.")
            elif action == "upload":
                self.status.set(f"Tác vụ {result['status']}: {result['video_url']}; {result['error'] or 'không lỗi' }.")
                if self.app.state and self.app.state.project.id == result["project_id"]:
                    for key in ("status", "video_id", "video_url"):
                        self.values[key].set(result[key])
                    self.capture()
                    self.app._save()
        if self.app.root.winfo_exists():
            self.app.root.after(300, self.poll)
