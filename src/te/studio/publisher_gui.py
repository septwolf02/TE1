"""Compact publisher dialog; all browser and file hashing work stays off Tk's thread."""
from __future__ import annotations

from pathlib import Path
import shutil
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .publisher import PublisherStore, file_identity, normalize_metadata
from .youtube_studio import YouTubeStudioBrowser, StudioSessionExpired


class PublisherController:
    def __init__(self, app):
        self.app = app
        self.store = PublisherStore(app.service.config_path)
        self.browser = YouTubeStudioBrowser(self.store.root / "browser")
        self.events = Queue()
        self.busy = False
        self.values = {key: tk.StringVar() for key in normalize_metadata({})}
        self.status = tk.StringVar(value="Chưa kết nối hoặc xác minh phiên Studio.")
        self.channel_name = tk.StringVar()
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
        ttk.Label(box, text="Hồ sơ trình duyệt riêng trong cấu hình TE Studio; đăng nhập Google trực tiếp trong trình duyệt.",
                  wraplength=850).grid(row=0, column=0, columnspan=3, sticky="w")
        for column, (label, command) in enumerate((("Kết nối Google", self.connect),
            ("Quản lý tài khoản", self.connect), ("Làm mới danh sách kênh", self.verify))):
            ttk.Button(box, text=label, command=command).grid(row=1, column=column, sticky="w", padx=4, pady=4)
        ttk.Button(box, text="Ngắt kết nối", command=self.disconnect).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(box, textvariable=self.status, wraplength=850).grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(box, text="Email Google: chưa xác minh tự động. Hãy kiểm tra tài khoản đang đăng nhập trong Studio.").grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(box, text="ID kênh từ URL studio.youtube.com/channel/UC…").grid(row=4, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["channel_id"], width=35).grid(row=4, column=1, sticky="ew")
        ttk.Label(box, text="Tên kênh (do bạn nhập)").grid(row=5, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.channel_name).grid(row=5, column=1, sticky="ew")
        ttk.Label(box, text="Kênh đã xác minh trên máy").grid(row=6, column=0, sticky="w")
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
        ttk.Label(box, text="Phase 4A: chỉ tải thử Riêng tư. Đăng ngay, hẹn giờ, thumbnail, tags, playlist, kiếm tiền và end screen chưa được tự động áp dụng.",
                  wraplength=850).grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        row += 1
        ttk.Button(box, text="Lưu thông tin xuất bản", command=self.save).grid(row=row, column=0, sticky="w")
        ttk.Button(box, text="Tải thử Riêng tư", command=self.upload).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(box, textvariable=self.values["status"]).grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["video_id"], state="readonly", width=16).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(box, text="YouTube URL").grid(row=row, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.values["video_url"], state="readonly").grid(row=row, column=1, sticky="ew")
        self.refresh_channels()
        self.app.body = host

    def refresh_channels(self):
        channels = self.store.channels()
        self.channel_combo["values"] = [f"{c['name']} — {c['id']}" for c in channels]

    def _select_channel(self, _event=None):
        index = self.channel_combo.current()
        channels = self.store.channels()
        if 0 <= index < len(channels):
            self.values["channel_id"].set(channels[index]["id"])
            self.channel_name.set(channels[index]["name"])

    def choose(self, key):
        path = filedialog.askopenfilename(parent=self.app._details["publisher"],
            filetypes=[("MP4", "*.mp4")] if key == "video_path" else [("Ảnh", "*.png *.jpg *.jpeg *.webp")])
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
        self.status.set("Đang xử lý trong trình duyệt riêng…")
        def run():
            try:
                result = worker()
                self.events.put((action, result, None))
            except Exception as exc:
                self.events.put((action, None, exc))
        Thread(target=run, daemon=True).start()

    def connect(self):
        self._start("connect", self.browser.connect)

    def disconnect(self):
        if self.busy:
            self.status.set("Đợi thao tác YouTube hiện tại hoàn tất trước khi ngắt kết nối.")
            return
        if not messagebox.askyesno("Ngắt kết nối Google", "Xóa phiên trình duyệt YouTube riêng của TE Studio trên máy này?",
                                   parent=self.app._details["publisher"]):
            return
        try:
            if self.browser.profile_dir.exists():
                shutil.rmtree(self.browser.profile_dir)
        except OSError as exc:
            messagebox.showerror("YouTube", f"Không xóa được phiên trình duyệt: {exc}", parent=self.app.root)
            return
        self.store.data["channels"] = []
        self.store._save()
        self.refresh_channels()
        self.status.set("Đã ngắt kết nối và xóa phiên trình duyệt riêng. Tác vụ cũ vẫn được giữ.")

    def verify(self):
        channel_id = self.values["channel_id"].get().strip()
        name = self.channel_name.get().strip()
        if not name:
            messagebox.showerror("YouTube", "Nhập tên kênh hiển thị để nhận diện; ID và quyền upload sẽ được xác minh trong Studio.")
            return
        def work():
            result = self.browser.verify_channel(channel_id)
            if not result["upload_control_verified"]:
                raise ValueError("Không xác minh được quyền Upload.")
            self.store.add_verified_channel(channel_id, name)
            return result
        self._start("verify", work)

    def upload(self):
        state = self.app.state
        if state is None:
            return
        self.capture()
        if not self.app._save():
            return
        meta = normalize_metadata(state.publisher)
        if meta["mode"] != "immediate":
            messagebox.showerror("YouTube", "Phase 4A chưa tải thử tác vụ hẹn giờ. Chọn immediate để tải thử Riêng tư.")
            return
        if not messagebox.askyesno("Tải thử Riêng tư", f"Tải video lên kênh {meta['channel_id']} ở chế độ Riêng tư?\n\n"
                "TE Studio sẽ không đăng công khai. Nếu thao tác bị ngắt, hãy kiểm tra Studio trước khi thử lại.",
                parent=self.app._details["publisher"]):
            return
        project_id = state.project.id
        def work():
            identity = file_identity(Path(meta["video_path"]))
            job = self.store.prepare(project_id, meta, identity)
            if job["status"] != "ready":
                raise ValueError(f"Tác vụ này đã tồn tại ({job['status']}); kiểm tra Studio trước khi tạo tải lên khác.")
            # Check the session before reserving an upload attempt, so an expired
            # login can be reconnected without locking an unstarted job.
            self.browser.verify_channel(job["channel_id"])
            # Reserve before any file selection or upload in the browser.
            self.store.begin_attempt(job["id"])
            try:
                result = self.browser.upload_private(job)
            except Exception as exc:
                self.store.update(job["id"], status="needs_attention", error=str(exc))
                raise
            return self.store.update(job["id"], status="private_test", video_id=result["video_id"],
                video_url=result["video_url"], last_confirmed_operation="private_upload_confirmed")
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
                if isinstance(error, StudioSessionExpired):
                    self.status.set("Phiên Studio hết hạn. Bấm Kết nối Google để đăng nhập lại.")
                messagebox.showerror("YouTube", str(error), parent=self.app.root)
            elif action == "connect":
                self.status.set(result)
            elif action == "verify":
                self.status.set(f"Đã xác minh quyền mở Upload cho {result['id']}; tên kênh do bạn nhập.")
                self.refresh_channels()
            elif action == "upload":
                self.status.set("YouTube Studio đã trả video ID; video tải thử ở chế độ Riêng tư.")
                if self.app.state and self.app.state.project.id == result["project_id"]:
                    for key in ("status", "video_id", "video_url"):
                        self.values[key].set(result[key])
                    self.capture()
                    self.app._save()
        if self.app.root.winfo_exists():
            self.app.root.after(300, self.poll)
