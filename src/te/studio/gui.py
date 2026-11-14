"""Tkinter editor and local video production controls with Vietnamese labels."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from te.project.models import SectionName
from te.subtitles import split_japanese_sentences
from te.tts import VoicevoxProvider
from te.studio.service import StudioProject, StudioService, MOTION_PRESETS, TRANSITION_PRESETS
from te.media.key_service import KeyPlacementService
from te.studio.rendering import StudioRenderService
from te.render.service import RenderError
from te.studio.ai_gui import SpeakerAIController
from te.studio.multi_voice import speech_plan
from te.studio.render_queue import RenderQueue


SECTIONS = tuple(SectionName)
MOTION_LABELS = {
    "Tự động: zoom và pan": "mixed", "Chỉ zoom vào/ra": "zoom",
    "Chỉ pan": "pan", "Không chuyển động": "none",
}
TRANSITION_LABELS = {"Cắt thẳng": "cut", "Mờ dần": "fade", "Chuyển mờ": "crossfade"}
RENDER_ENCODER_LABELS = {"Tự động – ưu tiên GPU": "auto", "CPU libx264": "libx264",
                         "NVIDIA NVENC": "h264_nvenc"}


class StudioApp:
    def __init__(self, root: tk.Tk, service: StudioService | None = None):
        self.root = root
        self.service = service or StudioService()
        self.state: StudioProject | None = None
        self.active_section = SectionName.P1
        self._loading = False
        self._save_after: str | None = None
        self._images: list[ImageTk.PhotoImage] = []
        self._script_dirty = False
        self.speakers: list[dict] = []
        self._loaded_voice_fields: tuple | None = None
        self._save_status = tk.StringVar(value="Chưa mở dự án")
        self.video_name = tk.StringVar()
        self.channel = tk.StringVar()
        self.media_path = tk.StringVar()
        self.media_count = tk.StringVar(value="Chưa chọn thư mục ảnh")
        self.min_duration = tk.StringVar(value="7")
        self.max_duration = tk.StringVar(value="12")
        self.voice = tk.StringVar(value="青山龍星")
        self.narration_mode = tk.StringVar(value="Nhiều giọng A/B/C/D")
        self.style = tk.StringVar()
        self.speed = tk.StringVar(value="1.0")
        self.pitch = tk.StringVar(value="0.0")
        self.intonation = tk.StringVar(value="1.0")
        self.volume = tk.StringVar(value="1.0")
        self.subtitle_font = tk.StringVar(value="bold_japanese")
        self.subtitle_color = tk.StringVar(value="#FFFFFF")
        self.subtitle_chars = tk.StringVar(value="24")
        self.subtitle_size = tk.StringVar(value="156")
        self.subtitle_background_enabled = tk.BooleanVar(value=True)
        self.subtitle_background_opacity = tk.IntVar(value=40)
        self.headline_background_opacity = tk.IntVar(value=40)
        self.subtitle_background_percent = tk.StringVar(value="40%")
        self.headline_background_percent = tk.StringVar(value="40%")
        self.subtitle_lines = tk.StringVar(value="2 (cố định)")
        self.subtitle_position = tk.StringVar(value="bottom_center")
        self.font_path = tk.StringVar(value="")
        self.motion = tk.StringVar(value="Tự động: zoom và pan")
        self.transition = tk.StringVar(value="Cắt thẳng")
        self.render_encoder = tk.StringVar(value=next((label for label, value in RENDER_ENCODER_LABELS.items()
            if value == self.service.settings.get("render_encoder")), "Tự động – ưu tiên GPU"))
        self.headline_duration = tk.StringVar(value="7")
        self.summary = tk.StringVar(value="Tạo hoặc mở dự án để bắt đầu.")
        self.key_summary = tk.StringVar(value="0 ảnh KEY")
        self.ordinary_summary = tk.StringVar(value="Chưa chọn ảnh thường")
        self.intro_summary = tk.StringVar(value="P1 — | P2 — | P3 — | P4 —")
        self.voice_summary = tk.StringVar(value="Chưa chọn giọng")
        self.subtitle_summary = tk.StringVar(value="Phụ đề Nhật")
        self.output_summary = tk.StringVar(value="Chuyển động và thư mục xuất")
        self.section_status = tk.StringVar()
        self.render_status = tk.StringVar(value="Sẵn sàng tạo lời đọc hoặc render.")
        self.performance_summary = tk.StringVar(value="Thời gian dựng: chưa có báo cáo.")
        self._job_queue: Queue[tuple[str, object]] = Queue()
        self._job_running = False
        self._disabled_widgets: list[tuple[object, str]] = []
        self.renderer = StudioRenderService(self.service)
        self._queue_events: Queue[tuple[str, object]] = Queue()
        self._queue_pending = 0
        self._full_render_pending: set[str] = set()
        self.queue = RenderQueue(self.service, on_change=lambda: self._queue_events.put(("changed", None)))
        self.ai = SpeakerAIController(self)
        self.intro_paths = {section: tk.StringVar() for section in SECTIONS}
        self.root.title("TE Studio — Trình biên tập")
        self.root.geometry("1400x900")
        self.root.minsize(920, 690)
        self._details: dict[str, tk.Toplevel] = {}
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._choose_initial_project()
        self._refresh_queue()
        self.root.after(500, self._poll_queue)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        self.body = outer
        self._group_expand = False
        self._build_header()
        script_host = ttk.Frame(outer)
        script_host.pack(fill="x")
        self.body, self._group_expand = script_host, False
        self._build_script()
        self.body, self._group_expand = outer, False
        self.ai.build()
        modules = ttk.Frame(outer)
        modules.pack(fill="x", pady=4)
        modules.columnconfigure((0, 1), weight=1, uniform="module")
        cards = (("3. Ảnh KEY", self.key_summary, "keys"),
                 ("4. Ảnh thường", self.ordinary_summary, "media"),
                 ("5. Intro P1–P4", self.intro_summary, "intros"),
                 ("6. VOICEVOX", self.voice_summary, "voice"),
                 ("Phụ đề Nhật", self.subtitle_summary, "subtitles"),
                 ("Render/output settings", self.output_summary, "output"))
        for index, (title, status, key) in enumerate(cards):
            card = ttk.LabelFrame(modules, text=title, padding=5)
            card.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=2)
            card.columnconfigure(0, weight=1)
            ttk.Label(card, textvariable=status, wraplength=360).grid(row=0, column=0, sticky="ew", padx=4)
            ttk.Button(card, text="Chi tiết / Cài đặt", command=lambda k=key: self._show_detail(k)).grid(
                row=0, column=1, padx=4)
        self._build_render()
        self._create_detail("management", "Quản lý dự án và kênh", self._build_management)
        self._create_detail("keys", "Ảnh KEY", self._build_keys)
        self._create_detail("media", "Ảnh thường", self._build_media)
        self._create_detail("intros", "Intro P1–P4", self._build_intros)
        self._create_detail("voice", "VOICEVOX", self._build_voice)
        self._create_detail("subtitles", "Phụ đề Nhật", self._build_subtitles)
        self._create_detail("output", "Render/output settings", self._build_render_details)
        self._create_detail("queue", "Hàng đợi Render", self._build_queue)
        self.body = outer

    def _group(self, title: str) -> ttk.LabelFrame:
        group = ttk.LabelFrame(self.body, text=title, padding=10)
        group.pack(fill="both" if self._group_expand else "x", expand=self._group_expand, pady=3)
        return group

    def _create_detail(self, key: str, title: str, builder) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("980x640")
        dialog.minsize(700, 400)
        dialog.transient(self.root)
        dialog.withdraw()
        previous = self.body
        self.body = ttk.Frame(dialog, padding=10)
        self.body.pack(fill="both", expand=True)
        builder()
        footer = ttk.Frame(dialog, padding=8)
        footer.pack(fill="x")
        ttk.Button(footer, text="Lưu và đóng", command=lambda k=key: self._close_detail(k)).pack(side="right")
        ttk.Button(footer, text="Lưu", command=self._save_clicked).pack(side="right", padx=5)
        dialog.protocol("WM_DELETE_WINDOW", lambda k=key: self._close_detail(k))
        self._details[key] = dialog
        self.body = previous

    def _show_detail(self, key: str) -> None:
        if key == "output":
            self._refresh_performance_summary()
        dialog = self._details[key]
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()

    def _close_detail(self, key: str) -> None:
        if self.state and not self._save():
            return
        dialog = self._details[key]
        dialog.grab_release()
        dialog.withdraw()
        self._refresh_compact_summaries()

    def _build_header(self) -> None:
        box = self._group("1. Dự án")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Tên video").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Entry(box, textvariable=self.video_name).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(box, text="Kênh").grid(row=0, column=2, sticky="w", padx=4)
        self.channel_combo = ttk.Combobox(box, textvariable=self.channel, state="readonly", width=23)
        self.channel_combo.grid(row=0, column=3, sticky="ew", padx=4)
        self.channel_combo.bind("<<ComboboxSelected>>", lambda _e: self._channel_selected())
        actions = ttk.Frame(box)
        actions.grid(row=0, column=4, sticky="e")
        for label, command in (("Tạo", self._new_project), ("Mở", self._open_project),
                               ("Lưu", self._save_clicked),
                               ("Quản lý…", lambda: self._show_detail("management"))):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(box, textvariable=self._save_status).grid(row=1, column=0, columnspan=5,
                                                             sticky="w", pady=(2, 0))
        self.video_name.trace_add("write", lambda *_: self._dirty())
        self._refresh_channels()

    def _build_management(self) -> None:
        box = self._group("Dự án và kênh")
        actions = (("Đổi tên dự án", self._rename_project), ("Nhân bản dự án", self._duplicate_project),
                   ("Tạo kênh", self._create_channel), ("Đổi tên kênh", self._rename_channel),
                   ("Áp dụng mặc định kênh", self._apply_channel),
                   ("Lưu cài đặt vào kênh", self._save_channel),
                   ("Thư mục mặc định", self._configure_roots))
        for index, (label, command) in enumerate(actions):
            ttk.Button(box, text=label, command=command, width=29).grid(
                row=index // 2, column=index % 2, sticky="w", padx=5, pady=5)
        ttk.Label(box, textvariable=self.summary, wraplength=850).grid(row=4, column=0,
                                                                       columnspan=2, sticky="w", pady=10)

    def _build_script(self) -> None:
        box = self._group("2. Kịch bản tiếng Việt – tiếng Nhật")
        tabs = ttk.Frame(box)
        tabs.pack(fill="x")
        self.section_buttons = {}
        for section in SECTIONS:
            button = ttk.Button(tabs, text=section.value, command=lambda s=section: self._switch_section(s))
            button.pack(side="left", padx=3)
            self.section_buttons[section] = button
        ttk.Button(tabs, text="Nhập toàn bài Việt–Nhật", command=self._open_full_script_import).pack(
            side="left", padx=12)
        script_expanded = tk.BooleanVar(value=False)
        def toggle_script_height():
            expanded = not script_expanded.get()
            script_expanded.set(expanded)
            for widget in (self.vn_text, self.jp_text):
                widget.configure(height=12 if expanded else 6)
            size_button.configure(text="Thu gọn ô kịch bản" if expanded else "Mở rộng ô kịch bản")
        size_button = ttk.Button(tabs, text="Mở rộng ô kịch bản", command=toggle_script_height)
        size_button.pack(side="left", padx=4)
        ttk.Label(tabs, textvariable=self.section_status).pack(side="right")
        panel = ttk.Frame(box)
        panel.pack(fill="x", pady=3)
        panel.columnconfigure((0, 1), weight=1)
        ttk.Label(panel, text="Tiếng Việt").grid(row=0, column=0, sticky="w")
        ttk.Label(panel, text="Tiếng Nhật").grid(row=0, column=1, sticky="w")
        self.vn_text = tk.Text(panel, height=6, wrap="word", undo=True, font=("Segoe UI", 11))
        self.jp_text = tk.Text(panel, height=6, wrap="word", undo=True, font=("Yu Gothic", 11))
        self.vn_text.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.jp_text.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        for widget in (self.vn_text, self.jp_text):
            widget.bind("<KeyRelease>", lambda _e: self._script_changed())
            widget.bind("<FocusOut>", lambda _e: self._capture_section())
            widget.bind("<MouseWheel>", lambda event, w=widget:
                (w.yview_scroll(-int(event.delta / 120), "units"), "break")[1])

    def _open_full_script_import(self) -> None:
        if not self.state:
            self._fail("Hãy tạo hoặc mở một dự án trước.")
            return
        self._capture_section()
        dialog = tk.Toplevel(self.root)
        dialog.title("Nhập toàn bài Việt–Nhật")
        dialog.geometry("1050x760")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure((0, 1), weight=1)
        dialog.rowconfigure(1, weight=3)
        dialog.rowconfigure(3, weight=1)
        ttk.Label(dialog, text="Toàn bài tiếng Việt (mỗi nhãn P1–P4 đứng riêng một dòng)").grid(
            row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(dialog, text="Toàn bài tiếng Nhật (mỗi nhãn P1–P4 đứng riêng một dòng)").grid(
            row=0, column=1, sticky="w", padx=10, pady=5)
        vi_box = tk.Text(dialog, wrap="word", undo=True, font=("Segoe UI", 11))
        ja_box = tk.Text(dialog, wrap="word", undo=True, font=("Yu Gothic", 11))
        vi_box.grid(row=1, column=0, sticky="nsew", padx=(10, 5))
        ja_box.grid(row=1, column=1, sticky="nsew", padx=(5, 10))
        ttk.Label(dialog, text="Xem trước nhãn, nội dung bị thay thế/xóa và cảnh báo KEY").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 2))
        preview = tk.Text(dialog, wrap="word", state="disabled", height=10)
        preview.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=10)
        actions = ttk.Frame(dialog)
        actions.grid(row=4, column=0, columnspan=2, sticky="e", padx=10, pady=10)
        checked = None

        def input_text():
            return vi_box.get("1.0", "end-1c"), ja_box.get("1.0", "end-1c")

        def show_preview(value: str) -> None:
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", value)
            preview.configure(state="disabled")

        def validate() -> None:
            nonlocal checked
            checked = None
            vi, ja = input_text()
            try:
                plan = self.service.prepare_full_script_import(self._require_state(), vi, ja)
            except ValueError as exc:
                show_preview(f"Không thể chia bài: {exc}")
                confirm.configure(state="disabled")
                return
            lines = [f"Bản Việt: {', '.join(plan.vietnamese_labels)}",
                     f"Bản Nhật: {', '.join(plan.japanese_labels)}", ""]
            for section in SECTIONS:
                label = section.value
                new = plan.sections[label]
                old = self.state.sections[label]
                action = ("xóa nội dung cũ" if label in plan.cleared else
                          "thay thế nội dung cũ" if label in plan.overwritten else
                          "giữ nguyên" if old == new else "thêm nội dung")
                lines.append(f"{label}: Việt {'có' if new['vi'].strip() else 'trống'}, "
                             f"Nhật {'có' if new['ja'].strip() else 'trống'} — {action}")
            if plan.warnings:
                lines.extend(("", *plan.warnings))
            if plan.collapsed:
                lines.append("Gộp các đoạn cũ không có dữ liệu gắn: " + ", ".join(plan.collapsed))
            show_preview("\n".join(lines))
            checked = (vi, ja, plan)
            confirm.configure(state="normal")

        def apply() -> None:
            if checked is None or input_text() != checked[:2]:
                show_preview("Nội dung đã thay đổi. Hãy bấm Kiểm tra và chia P1–P4 lần nữa.")
                confirm.configure(state="disabled")
                return
            plan = checked[2]
            if plan.overwritten or plan.cleared or plan.key_review:
                details = []
                if plan.overwritten:
                    details.append("Thay thế: " + ", ".join(plan.overwritten))
                if plan.cleared:
                    details.append("Xóa: " + ", ".join(plan.cleared))
                if plan.key_review:
                    details.append(f"{len(plan.key_review)} KEY có thể cần xác nhận lại.")
                if not messagebox.askyesno("Xác nhận thay nội dung", "\n".join(details) +
                                           "\n\nTiếp tục nhập?", parent=dialog):
                    return
            try:
                self._apply_fields()
                self.service.apply_full_script_import(self._require_state(), plan)
            except Exception as exc:
                messagebox.showerror("Không thể nhập toàn bài", str(exc), parent=dialog)
                return
            if self._save_after:
                self.root.after_cancel(self._save_after)
                self._save_after = None
            dialog.destroy()
            self._load_state(self.state)
            self._save_status.set("Đã nhập và lưu toàn bài Việt–Nhật")

        ttk.Button(actions, text="Kiểm tra và chia P1–P4", command=validate).pack(side="left", padx=5)
        confirm = ttk.Button(actions, text="Xác nhận nhập", command=apply, state="disabled")
        confirm.pack(side="left", padx=5)
        ttk.Button(actions, text="Hủy", command=dialog.destroy).pack(side="left", padx=5)

    def _build_keys(self) -> None:
        box = self._group("3. Ảnh KEY và câu tiết lộ")
        ttk.Button(box, text="Thêm ảnh KEY…", command=self._add_key).pack(anchor="w")
        ttk.Label(box, text="Chọn câu tiếng Nhật, nhập câu tiếng Việt tương ứng rồi bấm Xác nhận. Không tự ghép câu.").pack(anchor="w", pady=3)
        listing = ttk.Frame(box)
        listing.pack(fill="both", expand=True)
        canvas = tk.Canvas(listing, height=390, highlightthickness=0)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.key_rows = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.key_rows, anchor="nw")
        self.key_rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

    def _build_media(self) -> None:
        box = self._group("4. Thư mục ảnh thường của video này")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="Chọn thư mục…", command=self._choose_media_folder).pack(side="left")
        ttk.Button(row, text="Tải lại danh sách", command=self._reload_media_folder).pack(side="left", padx=5)
        ttk.Button(row, text="Bỏ thư mục…", command=self._clear_media_folder).pack(side="left", padx=5)
        ttk.Button(row, text="Xáo trộn lại ảnh", command=self._reshuffle_images).pack(side="left", padx=5)
        ttk.Label(row, textvariable=self.media_path, wraplength=700).pack(side="left", padx=8)
        ttk.Label(box, textvariable=self.media_count).pack(anchor="w", pady=3)
        duration = ttk.Frame(box)
        duration.pack(anchor="w")
        ttk.Label(duration, text="Thời lượng ảnh tối thiểu (giây)").pack(side="left")
        ttk.Entry(duration, textvariable=self.min_duration, width=7).pack(side="left", padx=5)
        ttk.Label(duration, text="Tối đa (giây)").pack(side="left")
        ttk.Entry(duration, textvariable=self.max_duration, width=7).pack(side="left", padx=5)
        self.media_gallery = ttk.Frame(box)
        self.media_gallery.pack(anchor="w", pady=3)
        for variable in (self.min_duration, self.max_duration):
            variable.trace_add("write", lambda *_: self._dirty())

    def _build_intros(self) -> None:
        box = self._group("5. Video mở đầu từng phần (tùy chọn)")
        for section in SECTIONS:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"Intro {section.value}", width=10).pack(side="left")
            ttk.Button(row, text="Chọn video…", command=lambda s=section: self._choose_intro(s)).pack(side="left")
            ttk.Button(row, text="Xóa chọn", command=lambda s=section: self._clear_intro(s)).pack(side="left", padx=4)
            enabled = tk.BooleanVar(value=True)
            ttk.Checkbutton(row, text="Dùng", variable=enabled,
                command=lambda s=section, v=enabled: self._set_intro_preference(s, v.get())).pack(side="left")
            if not hasattr(self, "intro_enabled"):
                self.intro_enabled = {}
            self.intro_enabled[section] = enabled
            ttk.Label(row, textvariable=self.intro_paths[section], wraplength=750).pack(side="left", padx=5)

    def _build_voice(self) -> None:
        box = self._group("Giọng VOICEVOX")
        mode = ttk.Frame(box)
        mode.pack(fill="x", pady=4)
        ttk.Label(mode, text="Chế độ lời đọc").pack(side="left")
        ttk.Combobox(mode, textvariable=self.narration_mode,
                     values=("Một giọng", "Nhiều giọng A/B/C/D"), state="readonly", width=27).pack(
                         side="left", padx=6)
        self.narration_mode.trace_add("write", lambda *_: self._dirty())
        top = ttk.Frame(box)
        top.pack(fill="x")
        ttk.Button(top, text="Dò lại VOICEVOX", command=self._refresh_voice).pack(side="left")
        ttk.Button(top, text="Nghe mẫu ngắn", command=self._voice_sample).pack(side="left", padx=4)
        self.voice_status = tk.StringVar(value="Có thể chỉnh sửa dự án khi VOICEVOX chưa chạy.")
        ttk.Label(top, textvariable=self.voice_status).pack(side="left", padx=8)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Giọng").pack(side="left")
        self.voice_combo = ttk.Combobox(row, textvariable=self.voice, width=22)
        self.voice_combo.pack(side="left", padx=5)
        self.voice_combo.bind("<<ComboboxSelected>>", lambda _e: self._speaker_selected())
        ttk.Label(row, text="Kiểu giọng").pack(side="left")
        self.style_combo = ttk.Combobox(row, textvariable=self.style, width=18)
        self.style_combo.pack(side="left", padx=5)
        parameters = ttk.Frame(box)
        parameters.pack(fill="x", pady=5)
        for label, variable in (("Tốc độ", self.speed), ("Cao độ", self.pitch),
                                ("Ngữ điệu", self.intonation), ("Âm lượng", self.volume)):
            ttk.Label(parameters, text=label).pack(side="left", padx=(8, 0))
            ttk.Entry(parameters, textvariable=variable, width=7).pack(side="left", padx=3)
        for variable in (self.voice, self.style, self.speed, self.pitch, self.intonation,
                         self.volume):
            variable.trace_add("write", lambda *_: self._dirty())

    def _build_subtitles(self) -> None:
        box = self._group("Phụ đề Nhật")
        subtitle = ttk.Frame(box)
        subtitle.pack(fill="x")
        ttk.Label(subtitle, text="Phông phụ đề").pack(side="left")
        ttk.Entry(subtitle, textvariable=self.subtitle_font, width=25).pack(side="left", padx=5)
        ttk.Label(subtitle, text="Màu chữ").pack(side="left")
        ttk.Entry(subtitle, textvariable=self.subtitle_color, width=10).pack(side="left", padx=5)
        ttk.Label(subtitle, text="Ký tự/dòng (tối đa 2 dòng)").pack(side="left")
        ttk.Entry(subtitle, textvariable=self.subtitle_chars, width=5).pack(side="left", padx=5)
        ttk.Label(subtitle, text="Cỡ chữ phụ đề").pack(side="left", padx=(12, 2))
        ttk.Entry(subtitle, textvariable=self.subtitle_size, width=5).pack(side="left", padx=5)
        font = ttk.Frame(box)
        font.pack(fill="x", pady=3)
        ttk.Label(font, text="File phông Nhật (tùy chọn)").pack(side="left")
        ttk.Entry(font, textvariable=self.font_path, width=68).pack(side="left", padx=5)
        ttk.Button(font, text="Chọn file…", command=self._choose_font).pack(side="left")
        ttk.Label(box, text="Số dòng (theo project schema):").pack(anchor="w")
        ttk.Label(box, textvariable=self.subtitle_lines).pack(anchor="w")
        ttk.Label(box, text="Vị trí:").pack(anchor="w")
        ttk.Label(box, textvariable=self.subtitle_position).pack(anchor="w")
        regular = ttk.Frame(box)
        regular.pack(fill="x", pady=3)
        ttk.Checkbutton(regular, text="Bật nền phụ đề", variable=self.subtitle_background_enabled).pack(
            side="left")
        ttk.Label(regular, text="Độ đậm nền phụ đề").pack(side="left", padx=(15, 4))
        tk.Scale(regular, from_=0, to=100, resolution=1, orient="horizontal", showvalue=False,
                 variable=self.subtitle_background_opacity, length=180).pack(side="left")
        ttk.Label(regular, textvariable=self.subtitle_background_percent, width=5).pack(side="left")
        for label, value in (("Nhẹ", 25), ("Vừa", 40), ("Đậm", 60)):
            ttk.Button(regular, text=label,
                       command=lambda selected=value: self.subtitle_background_opacity.set(selected)).pack(
                           side="left", padx=2)
        headline = ttk.Frame(box)
        headline.pack(fill="x", pady=3)
        ttk.Label(headline, text="Độ đậm nền chữ mở đầu").pack(side="left")
        tk.Scale(headline, from_=0, to=100, resolution=1, orient="horizontal", showvalue=False,
                 variable=self.headline_background_opacity, length=180).pack(side="left", padx=(8, 0))
        ttk.Label(headline, textvariable=self.headline_background_percent, width=5).pack(side="left")
        def background_changed(*_):
            self.subtitle_background_percent.set(f"{self.subtitle_background_opacity.get()}%")
            self.headline_background_percent.set(f"{self.headline_background_opacity.get()}%")
            self._dirty()
        for variable in (self.subtitle_background_enabled, self.subtitle_background_opacity,
                         self.headline_background_opacity):
            variable.trace_add("write", background_changed)
        for variable in (self.subtitle_font, self.subtitle_color, self.subtitle_chars,
                         self.subtitle_size, self.font_path):
            variable.trace_add("write", lambda *_: self._dirty())

    def _choose_font(self) -> None:
        path = filedialog.askopenfilename(title="Chọn phông tiếng Nhật", parent=self.root,
            filetypes=[("Font", "*.otf *.ttf *.ttc"), ("Tất cả", "*.*")])
        if path:
            self.font_path.set(path)

    def _build_motion(self) -> None:
        box = self._group("7. Chuyển động ảnh và chuyển cảnh")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text="Chuyển động").pack(side="left")
        ttk.Combobox(row, textvariable=self.motion, values=list(MOTION_LABELS),
                     state="readonly", width=29).pack(side="left", padx=6)
        ttk.Label(row, text="Chuyển cảnh").pack(side="left")
        ttk.Combobox(row, textvariable=self.transition, values=list(TRANSITION_LABELS),
                     state="readonly", width=16).pack(side="left", padx=6)
        for variable in (self.motion, self.transition):
            variable.trace_add("write", lambda *_: self._dirty())

    def _build_render(self) -> None:
        box = self._group("Tạo lời đọc và render")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=3)
        for label, action in (("Tạo / cập nhật lời đọc Nhật", self._generate_narration),
                              ("Xem nhanh 960×540", self._render_preview),
                              ("Xuất Full HD 1920×1080", self._render_full)):
            ttk.Button(row, text=label, command=action).pack(side="left", padx=4)
        ttk.Button(row, text="Mở thư mục xuất", command=self._open_output_folder).pack(side="left", padx=4)
        ttk.Button(row, text="Cài đặt / Log", command=lambda: self._show_detail("output")).pack(side="left", padx=4)
        ttk.Button(row, text="Hàng đợi Render", command=lambda: self._show_detail("queue")).pack(side="left", padx=4)
        ttk.Label(box, textvariable=self.render_status).pack(anchor="w", pady=1)

    def _build_render_details(self) -> None:
        self._build_motion()
        box = self._group("Output và chẩn đoán")
        choice = ttk.Frame(box)
        choice.pack(anchor="w", pady=3)
        ttk.Label(choice, text="Bộ mã hóa Full HD").pack(side="left")
        encoder_combo = ttk.Combobox(choice, textvariable=self.render_encoder,
            values=tuple(RENDER_ENCODER_LABELS), state="readonly", width=24)
        encoder_combo.pack(side="left", padx=6)
        def save_encoder(_event):
            self.service.settings["render_encoder"] = RENDER_ENCODER_LABELS[self.render_encoder.get()]
            self.service.save_settings()
        encoder_combo.bind("<<ComboboxSelected>>", save_encoder)
        headline_row = ttk.Frame(box)
        headline_row.pack(anchor="w", pady=3)
        ttk.Label(headline_row, text="Tiêu đề mở đầu (giây)").pack(side="left")
        ttk.Combobox(headline_row, textvariable=self.headline_duration,
            values=("5", "6", "7", "8", "9", "10"), state="readonly", width=5).pack(side="left", padx=6)
        self.headline_duration.trace_add("write", lambda *_: self._dirty())
        ttk.Label(box, textvariable=self.performance_summary, wraplength=900).pack(anchor="w", pady=3)
        ttk.Button(box, text="Mở báo cáo thời gian", command=self._open_performance_report).pack(anchor="w")
        files = ttk.Frame(box)
        files.pack(fill="x", pady=3)
        for label, action in (("Mở bản xem nhanh", self._open_preview),
                              ("Mở video cuối", self._open_final),
                              ("Mở thư mục xuất", self._open_output_folder)):
            ttk.Button(files, text=label, command=action).pack(side="left", padx=4)
        self.render_log = tk.Text(box, height=5, state="disabled", wrap="word")
        self.render_log.pack(fill="both", expand=True)
        ttk.Button(box, text="Thêm vào hàng đợi", command=self._enqueue_render).pack(anchor="w")

    def _build_queue(self) -> None:
        box = self._group("Hàng đợi Render • Full HD tuần tự")
        controls = ttk.Frame(box)
        controls.pack(fill="x", pady=4)
        actions = (("Thêm vào hàng đợi", self._enqueue_render),
                   ("Bắt đầu / Tiếp tục", self._queue_start),
                   ("Tạm dừng sau tác vụ hiện tại", self._queue_pause),
                   ("Hủy tác vụ đang chạy", self._queue_cancel),
                   ("Xóa tác vụ đang chờ", self._queue_remove),
                   ("Thử lại tác vụ lỗi", self._queue_retry),
                   ("Cập nhật tác vụ từ project", self._queue_update))
        for index, (label, command) in enumerate(actions):
            ttk.Button(controls, text=label, command=command).grid(
                row=index // 4, column=index % 4, sticky="w", padx=3, pady=3)
        columns = ("order", "project", "status", "stage", "encoder", "started", "ended")
        self.queue_tree = ttk.Treeview(box, columns=columns, show="headings", height=13)
        for key, title, width in (("order", "#", 35), ("project", "Project", 145),
                                  ("status", "Trạng thái", 85), ("stage", "Bước", 220),
                                  ("encoder", "Encoder", 95), ("started", "Bắt đầu UTC", 135),
                                  ("ended", "Kết thúc UTC", 135)):
            self.queue_tree.heading(key, text=title)
            self.queue_tree.column(key, width=width)
        self.queue_tree.pack(fill="both", expand=True, pady=4)
        self.queue_tree.bind("<<TreeviewSelect>>", lambda _event: self._queue_selection())
        self.queue_details = tk.StringVar(value="Chọn tác vụ để xem MP4, lỗi và báo cáo thời gian.")
        ttk.Label(box, textvariable=self.queue_details, wraplength=850).pack(anchor="w", pady=4)
        files = ttk.Frame(box)
        files.pack(anchor="w")
        ttk.Button(files, text="Mở thư mục đầu ra", command=self._queue_open_output).pack(side="left", padx=4)
        ttk.Button(files, text="Mở báo cáo thời gian", command=self._queue_open_performance).pack(side="left", padx=4)

    def _selected_queue_job(self) -> dict | None:
        selected = self.queue_tree.selection()
        return next((job for job in self.queue.jobs() if selected and job["id"] == selected[0]), None)

    def _queue_selection(self) -> None:
        job = self._selected_queue_job()
        if job:
            self.queue_details.set(f"MP4: {job['output_path'] or 'chưa có'} • "
                f"Báo cáo: {job['performance_path'] or 'chưa có'} • "
                f"Lỗi: {job['error'] or 'không'}")

    def _refresh_queue(self) -> None:
        if not hasattr(self, "queue_tree"):
            return
        selected = self.queue_tree.selection()
        self.queue_tree.delete(*self.queue_tree.get_children())
        for index, job in enumerate(self.queue.jobs(), 1):
            def when(value):
                return value.replace("T", " ")[:19] if value else ""
            self.queue_tree.insert("", "end", iid=job["id"], values=(index, job["project_name"],
                job["status"], job["stage"], job["actual_encoder"] or job["selected_encoder"],
                when(job["started_at"]), when(job["ended_at"])))
        if selected and self.queue_tree.exists(selected[0]):
            self.queue_tree.selection_set(selected[0])
        self._queue_selection()
        if self.queue.last_error:
            self.queue_details.set("Lỗi hàng đợi: " + self.queue.last_error)

    def _poll_queue(self) -> None:
        changed = False
        selected = None
        error = None
        while True:
            try:
                kind, value = self._queue_events.get_nowait()
            except Empty:
                break
            changed = True
            if kind == "select":
                selected = str(value)
                self._queue_pending = max(0, self._queue_pending - 1)
            elif kind == "full_select":
                selected, project_id, start_error = value
                self._full_render_pending.discard(project_id)
                self._queue_pending = max(0, self._queue_pending - 1)
                message = ("Đã thêm Full HD vào hàng đợi; đang render hoặc chờ đến lượt."
                    if not start_error else "Đã thêm Full HD vào hàng đợi; chưa thể bắt đầu: " + start_error)
                self.render_status.set(message)
                if start_error:
                    error = start_error
            elif kind == "updated":
                self._queue_pending = max(0, self._queue_pending - 1)
            elif kind == "full_error":
                project_id, error = value
                self._full_render_pending.discard(project_id)
                self._queue_pending = max(0, self._queue_pending - 1)
                self.render_status.set("Không thể thêm Full HD vào hàng đợi: " + error)
            elif kind == "error":
                error = str(value)
                self._queue_pending = max(0, self._queue_pending - 1)
        if changed:
            self._refresh_queue()
        if selected and self.queue_tree.exists(selected):
            self.queue_tree.selection_set(selected)
            self._queue_selection()
        if error:
            self._fail(error)
        if self.root.winfo_exists():
            self.root.after(500, self._poll_queue)

    def _enqueue_render(self, *, auto_start: bool = False) -> None:
        if auto_start and self.state:
            project_id = self.state.project.id
            if project_id in self._full_render_pending:
                self.render_status.set("Full HD của project này đang được thêm vào hàng đợi.")
                return
            if any(job["project_id"] == project_id and job["status"] in ("queued", "running")
                   for job in self.queue.jobs()):
                if self._queue_start():
                    self.render_status.set("Full HD của project này đã có trong hàng đợi; đang chạy hoặc chờ đến lượt.")
                return
        if not self.state or not self._save():
            return
        snapshot = deepcopy(self.state)
        self.queue_details.set("Đang tạo snapshot và kiểm tra media…")
        if auto_start:
            self._full_render_pending.add(snapshot.project.id)
            self.render_status.set("Đang tạo snapshot Full HD để thêm vào hàng đợi…")
        self._queue_pending += 1
        def worker():
            try:
                job = self.queue.enqueue(snapshot)
            except Exception as exc:
                self._queue_events.put(("full_error", (snapshot.project.id, str(exc))) if auto_start
                                       else ("error", str(exc)))
            else:
                if auto_start:
                    try:
                        self.queue.start()
                    except Exception as exc:
                        start_error = str(exc)
                    else:
                        start_error = None
                    self._queue_events.put(("full_select", (job["id"], snapshot.project.id, start_error)))
                else:
                    self._queue_events.put(("select", job["id"]))
        Thread(target=worker, daemon=True).start()

    def _queue_start(self) -> bool:
        try:
            self.queue.start()
        except Exception as exc:
            self._fail(str(exc))
            return False
        return True

    def _queue_pause(self) -> None:
        self.queue.pause_after_current()
        self.queue_details.set("Sẽ dừng sau tác vụ đang chạy; các tác vụ chờ được giữ lại.")

    def _queue_cancel(self) -> None:
        self.queue.cancel_current()
        self.queue_details.set("Đã yêu cầu hủy tác vụ; hàng đợi sẽ tiếp tục nếu chưa tạm dừng.")

    def _queue_remove(self) -> None:
        job = self._selected_queue_job()
        if job:
            try:
                self.queue.remove_queued(job["id"])
            except Exception as exc:
                self._fail(str(exc))

    def _queue_retry(self) -> None:
        job = self._selected_queue_job()
        if job:
            try:
                self.queue.retry(job["id"])
            except Exception as exc:
                self._fail(str(exc))

    def _queue_update(self) -> None:
        job = self._selected_queue_job()
        if not job or not self.state or not self._save():
            return
        snapshot = deepcopy(self.state)
        self.queue_details.set("Đang cập nhật snapshot từ project…")
        self._queue_pending += 1
        def worker():
            try:
                self.queue.update_from_project(job["id"], snapshot)
            except Exception as exc:
                self._queue_events.put(("error", str(exc)))
            else:
                self._queue_events.put(("updated", None))
        Thread(target=worker, daemon=True).start()

    def _queue_open_output(self) -> None:
        job = self._selected_queue_job()
        output = Path(job["output_path"]).parent if job and job["output_path"] else \
            Path(job["configuration"]["output_root"] if job else self.service.settings["output_root"])
        output.mkdir(parents=True, exist_ok=True)
        self._open_path(output)

    def _queue_open_performance(self) -> None:
        job = self._selected_queue_job()
        if job and job["performance_path"]:
            self._open_path(Path(job["performance_path"]))
        else:
            self._fail("Tác vụ chưa có báo cáo thời gian dựng.")

    def _choose_initial_project(self) -> None:
        self._new_project()

    def _fail(self, message: str) -> None:
        self._save_status.set(f"Lỗi: {message}")
        messagebox.showerror("TE Studio", message, parent=self.root)

    def _require_state(self) -> StudioProject:
        if self.state is None:
            raise ValueError("Hãy tạo hoặc mở một dự án trước.")
        return self.state

    def _capture_section(self) -> None:
        if self._loading or self.state is None:
            return
        self.service.set_section(self.state, self.active_section,
            japanese=self.jp_text.get("1.0", "end-1c"),
            vietnamese=self.vn_text.get("1.0", "end-1c"))

    def _script_changed(self) -> None:
        self._capture_section()
        self._script_dirty = True
        self._update_section_status()
        self._dirty()

    def _switch_section(self, section: SectionName) -> None:
        self._capture_section()
        self.active_section = section
        self._load_section()

    def _load_section(self) -> None:
        self._loading = True
        content = self.state.sections[self.active_section.value] if self.state else {"ja": "", "vi": ""}
        for widget, text in ((self.vn_text, content["vi"]), (self.jp_text, content["ja"])):
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
        self._loading = False
        self._update_section_status()

    def _update_section_status(self) -> None:
        if not self.state:
            return
        content = self.state.sections[self.active_section.value]
        count = len([x for x in split_japanese_sentences(content["ja"]) if x.strip()])
        self.section_status.set(f"{self.active_section.value}: {count} câu Nhật • " +
            ("Đã nhập hai bản" if content["ja"].strip() and content["vi"].strip() else "Chưa đủ hai bản"))
        for section in SECTIONS:
            value = self.state.sections[section.value]
            self.section_buttons[section].configure(text=f"{section.value} {'✓' if value['ja'].strip() else '–'}")

    def _load_state(self, state: StudioProject) -> None:
        self._loading = True
        self.state = state
        self.active_section = SectionName.P1
        self.video_name.set(state.project.name)
        self.channel.set(state.channel or "")
        self.media_path.set(state.project.media_library_path or "Chưa chọn")
        self.media_count.set(self.service.media_folder_status(state))
        self.min_duration.set(str(state.image_min_seconds))
        self.max_duration.set(str(state.image_max_seconds))
        self.headline_duration.set(str(int(state.headline_duration_seconds)))
        settings = state.project.tts_settings
        self.voice.set(settings.voice_id)
        self.narration_mode.set("Nhiều giọng A/B/C/D" if state.narration_mode == "multi" else "Một giọng")
        self.style.set(settings.style or "")
        self.speed.set(str(settings.speaking_rate))
        self.pitch.set(str(settings.pitch))
        self.intonation.set(str(settings.intonation))
        self.volume.set(str(settings.volume))
        self.subtitle_font.set(state.project.subtitle_style.get("font", "bold_japanese"))
        self.subtitle_color.set(state.project.subtitle_style.get("text_color", "#FFFFFF"))
        self.subtitle_chars.set(str(state.project.subtitle_style.get("max_chars_per_line", 24)))
        self.subtitle_size.set(str(state.subtitle_size))
        style = state.project.subtitle_style
        self.subtitle_background_enabled.set(style.get("background_enabled", True))
        self.subtitle_background_opacity.set(style.get("background_opacity", 47))
        self.headline_background_opacity.set(style.get("headline_background_opacity", 70))
        self.subtitle_lines.set(str(state.project.subtitle_style.get("max_lines", 2)) + " (cố định)")
        self.subtitle_position.set(state.project.subtitle_style.get("position", "bottom_center"))
        self.font_path.set(state.font_path or "")
        self.motion.set(next((label for label, value in MOTION_LABELS.items()
                              if value == state.motion_preset), next(iter(MOTION_LABELS))))
        self.transition.set(next((label for label, value in TRANSITION_LABELS.items()
                                  if value == state.transition), next(iter(TRANSITION_LABELS))))
        for section in SECTIONS:
            path = self.service.intro_path(state, section)
            self.intro_paths[section].set(("Thiếu file: " if path and not Path(path).is_file() else "") +
                                          (path or "Chưa chọn"))
            self.intro_enabled[section].set(state.intro_preferences.get(section.value, True))
        self._loading = False
        self._load_section()
        self._script_dirty = False
        self._images.clear()
        self._loaded_voice_fields = (settings.voice_id, settings.style or "", settings.speaking_rate,
                                     settings.pitch, settings.intonation, settings.volume)
        self._refresh_key_rows()
        self._refresh_gallery()
        self._refresh_summary()
        self._refresh_performance_summary()
        self.ai.project_loaded()
        self._save_status.set(state.recovery_warning or f"Đã mở: {state.directory}")

    def _dirty(self) -> None:
        if self._loading or not self.state:
            return
        self._save_status.set("Chưa lưu — sẽ tự lưu sau khi ngừng nhập")
        if self._save_after:
            self.root.after_cancel(self._save_after)
        self._save_after = self.root.after(900, lambda: self._save(auto=True))

    def _apply_fields(self) -> None:
        state = self._require_state()
        self._capture_section()
        state.project.name = self.video_name.get().strip()
        state.channel = self.channel.get() or None
        state.narration_mode = ("multi" if self.narration_mode.get() == "Nhiều giọng A/B/C/D"
                                else "single")
        state.image_min_seconds = float(self.min_duration.get())
        state.image_max_seconds = float(self.max_duration.get())
        state.headline_duration_seconds = float(self.headline_duration.get())
        state.motion_preset = MOTION_LABELS[self.motion.get()]
        state.transition = TRANSITION_LABELS[self.transition.get()]
        settings = state.project.tts_settings
        values = (self.voice.get().strip(), self.style.get().strip(), float(self.speed.get()),
                  float(self.pitch.get()), float(self.intonation.get()), float(self.volume.get()))
        if values != self._loaded_voice_fields:
            settings.provider = "voicevox"
            settings.sample_rate = 24000
        settings.voice_id, style, settings.speaking_rate, settings.pitch, settings.intonation, settings.volume = values
        settings.style = style or None
        state.project.subtitle_style["font"] = self.subtitle_font.get().strip()
        state.project.subtitle_style["text_color"] = self.subtitle_color.get().strip()
        state.project.subtitle_style["max_chars_per_line"] = int(self.subtitle_chars.get())
        style = state.project.subtitle_style
        if "background_enabled" in style or not self.subtitle_background_enabled.get():
            style["background_enabled"] = self.subtitle_background_enabled.get()
        if "background_opacity" in style or self.subtitle_background_opacity.get() != 47:
            style["background_opacity"] = self.subtitle_background_opacity.get()
        if "headline_background_opacity" in style or self.headline_background_opacity.get() != 70:
            style["headline_background_opacity"] = self.headline_background_opacity.get()
        state.subtitle_size = int(self.subtitle_size.get())
        state.font_path = self.font_path.get().strip() or None

    def _save(self, *, auto: bool = False) -> bool:
        if not self.state:
            return False
        if self._save_after:
            self.root.after_cancel(self._save_after)
        self._save_after = None
        try:
            self._save_status.set("Đang lưu…")
            self._apply_fields()
            self.service.save_project(self.state)
            current = self.state.project.tts_settings
            self._loaded_voice_fields = (current.voice_id, current.style or "", current.speaking_rate,
                                         current.pitch, current.intonation, current.volume)
            self._save_status.set("Đã lưu")
            self._refresh_summary()
            if self._script_dirty:
                self._script_dirty = False
                self._refresh_key_rows()
            return True
        except Exception as exc:
            self._save_status.set(f"Chưa lưu: {exc}")
            if not auto:
                self._fail(str(exc))
            return False

    def _save_clicked(self) -> None:
        self._save()
        if self.state:
            self._refresh_key_rows()

    def _new_project(self) -> None:
        name = simpledialog.askstring("Dự án mới", "Tên video:", parent=self.root)
        if name is None:
            return
        if self.state and not self._save():
            return
        try:
            self._load_state(self.service.create_project(name, channel=self.channel.get() or None))
        except Exception as exc:
            self._fail(str(exc))

    def _open_project(self) -> None:
        if self.state and not self._save():
            return
        directory = filedialog.askdirectory(title="Chọn thư mục dự án TE", parent=self.root)
        if not directory:
            return
        try:
            self._load_state(self.service.open_project(Path(directory)))
        except Exception as exc:
            self._fail(str(exc))

    def _rename_project(self) -> None:
        if not self.state:
            return
        name = simpledialog.askstring("Đổi tên dự án", "Tên video mới:",
                                      initialvalue=self.video_name.get(), parent=self.root)
        if name:
            self.video_name.set(name)
            self._save()

    def _duplicate_project(self) -> None:
        if not self.state or not self._save():
            return
        name = simpledialog.askstring("Nhân bản dự án", "Tên bản sao:",
            initialvalue=f"{self.video_name.get()} - bản sao", parent=self.root)
        if not name:
            return
        try:
            self._load_state(self.service.duplicate_project(self.state, name=name))
        except Exception as exc:
            self._fail(str(exc))

    def _refresh_channels(self) -> None:
        self.channel_combo.configure(values=("",) + self.service.profile_names())

    def _create_channel(self) -> None:
        if self.state and not self._save():
            return
        name = simpledialog.askstring("Tạo kênh", "Tên kênh:", parent=self.root)
        if not name:
            return
        try:
            self.service.create_profile(name, self.state)
            self._refresh_channels()
            self.channel.set(name)
            if self.state:
                self.state.channel = name
                self._dirty()
        except Exception as exc:
            self._fail(str(exc))

    def _rename_channel(self) -> None:
        old = self.channel.get()
        if not old:
            return
        new = simpledialog.askstring("Đổi tên kênh", "Tên kênh mới:", initialvalue=old, parent=self.root)
        if not new:
            return
        try:
            self.service.rename_profile(old, new)
            self._refresh_channels()
            self.channel.set(new)
            if self.state:
                self.state.channel = new
                self._dirty()
        except Exception as exc:
            self._fail(str(exc))

    def _channel_selected(self) -> None:
        if self.state:
            self.state.channel = self.channel.get() or None
            self._dirty()

    def _apply_channel(self) -> None:
        if not self.state or not self.channel.get() or not self._save():
            return
        if not messagebox.askyesno("Áp dụng hồ sơ kênh", "Thay cài đặt giọng, phụ đề và hiệu ứng của dự án bằng mặc định kênh?",
                                   parent=self.root):
            return
        try:
            self.service.apply_profile(self.state, self.channel.get())
            self._load_state(self.state)
            self._dirty()
        except Exception as exc:
            self._fail(str(exc))

    def _save_channel(self) -> None:
        if not self.state or not self.channel.get() or not self._save():
            return
        try:
            self.service.save_profile_from_project(self.channel.get(), self.state)
            self._save_status.set("Đã lưu mặc định kênh")
        except Exception as exc:
            self._fail(str(exc))

    def _configure_roots(self) -> None:
        projects = filedialog.askdirectory(title="Thư mục dự án mặc định", parent=self.root,
            initialdir=self.service.settings["projects_root"])
        if not projects:
            return
        output = filedialog.askdirectory(title="Thư mục xuất video sau này", parent=self.root,
            initialdir=self.service.settings["output_root"])
        if not output:
            return
        try:
            self.service.set_directories(Path(projects), Path(output))
            self._save_status.set("Đã lưu thư mục mặc định")
        except Exception as exc:
            self._fail(str(exc))

    def _reload_media_folder(self) -> None:
        if not self.state or not self.state.project.media_library_path:
            self._fail("Dự án chưa chọn thư mục ảnh thường.")
            return
        self._choose_media_folder(Path(self.state.project.media_library_path))

    def _clear_media_folder(self) -> None:
        if not self.state or not self.state.project.media_library_path or not self._save():
            return
        root = Path(self.state.project.media_library_path)
        media_ids = {asset.id for asset in self.state.project.media if asset.category == "normal"
                     and Path(asset.path).is_relative_to(root)}
        count = sum(item.media_id in media_ids for item in self.state.project.media_assignments)
        if not messagebox.askyesno("Bỏ thư mục ảnh thường",
                f"Bỏ thư mục ảnh thường và {count} gán ảnh liên quan khỏi dự án? "
                "Ảnh gốc, KEY và intro được giữ nguyên.", parent=self._details["media"]):
            return
        try:
            self.service.clear_media_folder(self.state)
            self.media_path.set("Chưa chọn")
            self.media_count.set("Chưa chọn thư mục ảnh")
            self._refresh_gallery()
            self._refresh_summary()
        except Exception as exc:
            self._fail(str(exc))

    def _choose_media_folder(self, selected_path: Path | None = None) -> None:
        state = self.state
        if not state or not self._save():
            return
        path = selected_path or filedialog.askdirectory(title="Chọn thư mục ảnh thường cho video này", parent=self.root)
        if not path:
            return
        snapshot = deepcopy(state)
        self.media_count.set("Đang quét thư mục ảnh…")
        def work():
            return self.service.select_media_folder(snapshot, Path(path))
        def done(count):
            if self.state is state:
                state.project = snapshot.project
                self.media_path.set(str(path))
                self.media_count.set(f"{count} ảnh thường phù hợp" if count else "Thư mục chưa có ảnh thường phù hợp")
                self._save()
                self._refresh_gallery()
                self._refresh_summary()
        self._background(work, done)

    def _reshuffle_images(self) -> None:
        if not self.state or not self._save():
            return
        try:
            changed = self.service.reshuffle_images(self.state)
            self._refresh_summary()
            self._append_render_log(f"Đã xáo trộn {changed} ảnh tự động. Render lại để xem thứ tự mới.")
        except Exception as exc:
            self._fail(str(exc))

    def _thumbnail(self, path: Path, size=(92, 66)):
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail(size)
                photo = ImageTk.PhotoImage(image)
                self._images.append(photo)
                return photo
        except Exception:
            return None

    def _refresh_gallery(self) -> None:
        for child in self.media_gallery.winfo_children():
            child.destroy()
        if not self.state:
            return
        assets = [asset for asset in self.state.project.media if asset.available and
                  asset.media_type == "image" and asset.category == "normal"][:5]
        for asset in assets:
            photo = self._thumbnail(Path(asset.path))
            item = ttk.Frame(self.media_gallery)
            item.pack(side="left", padx=4)
            if photo:
                ttk.Label(item, image=photo).pack()
            ttk.Label(item, text=asset.filename[:16]).pack()

    def _add_key(self) -> None:
        if not self.state or not self._save():
            return
        path = filedialog.askopenfilename(title="Chọn ảnh KEY", parent=self.root,
            filetypes=[("Ảnh", "*.jpg *.jpeg *.png *.webp")])
        if not path:
            return
        try:
            self.service.add_key_draft(self.state, Path(path))
            self._refresh_key_rows()
            self._dirty()
        except Exception as exc:
            self._fail(str(exc))

    def _refresh_key_rows(self) -> None:
        for child in self.key_rows.winfo_children():
            child.destroy()
        if not self.state:
            return
        media = {asset.id: asset for asset in self.state.project.media}
        rows = ([("draft", item) for item in self.state.key_drafts] +
                [("placed", item) for item in self.state.project.key_placements])
        for kind, item in rows:
            media_id = item["media_id"] if kind == "draft" else item.media_id
            asset = media.get(media_id)
            if not asset:
                continue
            if kind == "draft":
                section = item.get("section", "P1")
                duration = item.get("duration_seconds")
                identifier = item["id"]
            else:
                unit = next((u for u in self.state.project.sentence_units if u.id == item.sentence_id), None)
                segment = next((s for s in self.state.project.segments if unit and s.id == unit.segment_id), None)
                section = segment.section.value if segment else "?"
                duration = item.duration_ms / 1000 if item.duration_ms is not None else None
                identifier = item.id
            line = ttk.Frame(self.key_rows, padding=4)
            line.pack(fill="x")
            ttk.Label(line, text=f"{section} | {asset.filename[:48]} | " +
                      self._key_duration_label(duration),
                      width=65).pack(side="left", padx=4)
            ttk.Button(line, text="Chỉnh sửa", command=lambda k=kind, i=identifier: self._open_key_entry(k, i)).pack(
                side="left", padx=4)
            ttk.Button(line, text="Xóa", command=lambda i=identifier: self._remove_key(i)).pack(side="left")
        self._refresh_compact_summaries()

    def _open_key_entry(self, kind: str, identifier: str) -> None:
        if not self.state:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Chỉnh sửa ảnh KEY")
        dialog.geometry("900x410")
        dialog.transient(self._details["keys"])
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=12)
        panel.pack(fill="both", expand=True)
        if kind == "draft":
            item = next(item for item in self.state.key_drafts if item["id"] == identifier)
            duration, offset, _, _ = self._render_key_draft(panel, item)
            ttk.Label(panel, text="Thời lượng (giây)").grid(row=4, column=1, sticky="w")
            ttk.Entry(panel, textvariable=duration, width=8).grid(row=4, column=2, sticky="w")
            ttk.Label(panel, text="Lệch (ms)").grid(row=5, column=1, sticky="w")
            ttk.Entry(panel, textvariable=offset, width=8).grid(row=5, column=2, sticky="w")
        else:
            item = next(item for item in self.state.project.key_placements if item.id == identifier)
            unit = next((u for u in self.state.project.sentence_units if u.id == item.sentence_id), None)
            ttk.Label(panel, text="Tiếng Nhật: " + (unit.synthesis_text if unit else "Câu đã thay đổi"),
                      wraplength=800).pack(anchor="w", pady=6)
            ttk.Label(panel, text="Tiếng Việt: " + item.confirmed_vietnamese_text,
                      wraplength=800).pack(anchor="w", pady=6)
            duration = tk.StringVar(value="" if item.duration_ms is None else str(item.duration_ms / 1000))
            offset = tk.StringVar(value=str(item.offset_ms))
            timing = ttk.Frame(panel)
            timing.pack(anchor="w", pady=8)
            for label, variable in (("Thời lượng (giây)", duration), ("Lệch (ms)", offset)):
                ttk.Label(timing, text=label).pack(side="left", padx=3)
                ttk.Entry(timing, textvariable=variable, width=8).pack(side="left", padx=3)
            def update():
                try:
                    KeyPlacementService.set_timing(self.state.project, self.state.directory,
                        item.id, offset_ms=int(offset.get()),
                        duration_ms=round(float(duration.get()) * 1000) if duration.get() else None)
                    self.service.save_project(self.state)
                    self._refresh_key_rows()
                    self._refresh_summary()
                    dialog.destroy()
                except Exception as exc:
                    self._fail(str(exc))
            ttk.Button(panel, text="Lưu thời điểm", command=update).pack(anchor="w", pady=5)
            ttk.Button(panel, text="Chọn lại câu", command=lambda: self._retarget_key(identifier)).pack(
                anchor="w", pady=5)
        ttk.Button(dialog, text="Đóng", command=dialog.destroy).pack(side="right", padx=12, pady=8)

    def _render_key_draft(self, panel, item):
        section = tk.StringVar(value=item.get("section", "P1"))
        selected_vi = tk.StringVar()
        selected_ja = tk.StringVar()
        suggestion = tk.StringVar(value="Chọn câu Việt để xem gợi ý.")
        vi_review = tk.StringVar(value="")
        ja_review = tk.StringVar(value="")
        duration = tk.StringVar(value="" if item.get("duration_seconds") is None else str(item["duration_seconds"]))
        offset = tk.StringVar(value=str(item.get("offset_ms", 0)))
        ttk.Combobox(panel, textvariable=section, values=[s.value for s in SECTIONS],
                     state="readonly", width=5).grid(row=0, column=2, padx=4)
        vi_combo = ttk.Combobox(panel, textvariable=selected_vi, width=48, state="readonly")
        vi_combo.grid(row=0, column=3, sticky="ew", padx=4)
        ja_combo = ttk.Combobox(panel, textvariable=selected_ja, width=48, state="readonly")
        ja_combo.grid(row=1, column=3, sticky="ew", padx=4)
        ttk.Label(panel, text="Chọn câu Việt").grid(row=0, column=1, sticky="w")
        ttk.Label(panel, text="Câu Nhật gợi ý").grid(row=1, column=1, sticky="w")
        ttk.Label(panel, textvariable=suggestion, wraplength=270).grid(row=1, column=2, sticky="w")
        ttk.Label(panel, textvariable=vi_review, wraplength=350).grid(row=3, column=2, sticky="nw")
        ttk.Label(panel, textvariable=ja_review, wraplength=350).grid(row=3, column=3, sticky="nw")
        vi_choices = {}
        ja_choices = {}
        initializing = True
        def remember(*_):
            if initializing:
                return
            vi_unit = vi_choices.get(selected_vi.get())
            ja_unit = ja_choices.get(selected_ja.get())
            item.update(section=section.get(), vietnamese_key=vi_unit.key if vi_unit else None,
                        vietnamese_text=vi_unit.text if vi_unit else "",
                        sentence_id=ja_unit.sentence_id if ja_unit else None,
                        duration_seconds=duration.get() or None, offset_ms=offset.get())
            self._dirty()
        def review(*_):
            vi_unit = vi_choices.get(selected_vi.get())
            ja_unit = ja_choices.get(selected_ja.get())
            vi_review.set("Tiếng Việt: " + (vi_unit.text if vi_unit else "Chưa chọn"))
            ja_review.set("Tiếng Nhật: " + (ja_unit.text if ja_unit else "Chưa chọn"))
            remember()
        def suggest(*_):
            vi_unit = vi_choices.get(selected_vi.get())
            if vi_unit:
                proposal = self.service.suggest_japanese(self.state, SectionName(section.get()), vi_unit.key)
                suggestion.set(proposal.message)
                selected_ja.set(next((label for label, unit in ja_choices.items()
                    if unit.sentence_id == proposal.sentence_id), ""))
            else:
                suggestion.set("Chọn câu Việt để xem gợi ý.")
                selected_ja.set("")
            review()
        def update_choices(*_):
            nonlocal initializing
            previous = initializing
            initializing = True
            vi_choices.clear()
            vi_choices.update((unit.label, unit) for unit in
                self.service.vietnamese_units(self.state, SectionName(section.get())))
            ja_choices.clear()
            ja_choices.update((unit.label, unit) for unit in
                self.service.japanese_choices(self.state, SectionName(section.get())))
            vi_combo.configure(values=list(vi_choices))
            ja_combo.configure(values=list(ja_choices))
            selected_vi.set(next((label for label, unit in vi_choices.items()
                if unit.key == item.get("vietnamese_key") or
                (not item.get("vietnamese_key") and unit.text == item.get("vietnamese_text"))), ""))
            selected_ja.set(next((label for label, unit in ja_choices.items()
                if unit.sentence_id == item.get("sentence_id")), ""))
            initializing = previous
            if not previous:
                suggest()
            else:
                vi_unit = vi_choices.get(selected_vi.get())
                if vi_unit:
                    suggestion.set(self.service.suggest_japanese(
                        self.state, SectionName(section.get()), vi_unit.key).message)
                review()
        update_choices()
        initializing = False
        def switch_section(*_):
            item["vietnamese_key"] = None
            item["vietnamese_text"] = ""
            item["sentence_id"] = None
            update_choices()
        section.trace_add("write", switch_section)
        selected_vi.trace_add("write", suggest)
        selected_ja.trace_add("write", review)
        for variable in (duration, offset):
            variable.trace_add("write", remember)
        def confirm():
            try:
                vi_unit = vi_choices.get(selected_vi.get())
                ja_unit = ja_choices.get(selected_ja.get())
                if not vi_unit:
                    raise ValueError("Chọn câu tiếng Việt mô tả ảnh KEY.")
                if not ja_unit:
                    raise ValueError("Cần chọn câu Nhật tương ứng.")
                if not messagebox.askyesno("Xác nhận cặp câu", f"Tiếng Việt: {vi_unit.text}\n\n"
                    f"Tiếng Nhật: {ja_unit.text}\n\nXác nhận cặp câu này cho ảnh KEY?", parent=self.root):
                    return
                self.service.confirm_key_draft(self.state, item["id"], section=SectionName(section.get()),
                    sentence_id=ja_unit.sentence_id, vietnamese_key=vi_unit.key,
                    offset_ms=int(offset.get()), duration_seconds=float(duration.get()) if duration.get() else None)
                self._refresh_key_rows()
                self._refresh_summary()
                panel.winfo_toplevel().destroy()
            except Exception as exc:
                self._fail(str(exc))
        ttk.Button(panel, text="Xác nhận VN–JP", command=confirm).grid(row=2, column=3, sticky="e")
        identifier = item["id"]
        status = "Chưa xác nhận"
        return duration, offset, identifier, status

    def _remove_key(self, identifier: str) -> None:
        if not self.state:
            return
        if not messagebox.askyesno("Xóa KEY", "Xóa cấu hình KEY khỏi video? File ảnh gốc sẽ được giữ nguyên.", parent=self.root):
            return
        try:
            self.service.remove_key(self.state, identifier)
            self._refresh_key_rows()
            self._refresh_summary()
        except Exception as exc:
            self._fail(str(exc))

    def _retarget_key(self, placement_id: str) -> None:
        if not self.state:
            return
        placement = next(item for item in self.state.project.key_placements if item.id == placement_id)
        dialog = tk.Toplevel(self.root)
        dialog.title("Xác nhận lại câu KEY")
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=12)
        panel.pack(fill="both", expand=True)
        section = tk.StringVar(value="P1")
        unit = next((u for u in self.state.project.sentence_units if u.id == placement.sentence_id), None)
        if unit:
            parent = next(s for s in self.state.project.segments if s.id == unit.segment_id)
            section.set(parent.section.value)
        selected_vi = tk.StringVar()
        selected_ja = tk.StringVar()
        suggestion = tk.StringVar(value="Chọn câu Việt để xem gợi ý.")
        vi_review = tk.StringVar(value="")
        ja_review = tk.StringVar(value="")
        ttk.Label(panel, text="Phần").grid(row=0, column=0, sticky="w")
        ttk.Combobox(panel, values=[s.value for s in SECTIONS], textvariable=section,
                     state="readonly", width=7).grid(row=0, column=1, sticky="w")
        ttk.Label(panel, text="Chọn câu Việt").grid(row=1, column=0, sticky="w")
        vi_combo = ttk.Combobox(panel, textvariable=selected_vi, state="readonly", width=75)
        vi_combo.grid(row=1, column=1, sticky="ew")
        ttk.Label(panel, text="Câu Nhật tương ứng").grid(row=2, column=0, sticky="w")
        ja_combo = ttk.Combobox(panel, textvariable=selected_ja, state="readonly", width=75)
        ja_combo.grid(row=2, column=1, sticky="ew")
        ttk.Label(panel, textvariable=suggestion, wraplength=450).grid(row=3, column=1, sticky="w")
        ttk.Label(panel, textvariable=vi_review, wraplength=450).grid(row=4, column=0, sticky="w")
        ttk.Label(panel, textvariable=ja_review, wraplength=450).grid(row=4, column=1, sticky="w")
        vi_choices = {}
        ja_choices = {}
        def review(*_):
            vi_unit = vi_choices.get(selected_vi.get())
            ja_unit = ja_choices.get(selected_ja.get())
            vi_review.set("Tiếng Việt: " + (vi_unit.text if vi_unit else "Chưa chọn"))
            ja_review.set("Tiếng Nhật: " + (ja_unit.text if ja_unit else "Chưa chọn"))
        def suggest(*_):
            vi_unit = vi_choices.get(selected_vi.get())
            if vi_unit:
                proposal = self.service.suggest_japanese(self.state, SectionName(section.get()), vi_unit.key)
                suggestion.set(proposal.message)
                selected_ja.set(next((label for label, choice in ja_choices.items()
                    if choice.sentence_id == proposal.sentence_id), ""))
            else:
                suggestion.set("Chọn câu Việt để xem gợi ý.")
                selected_ja.set("")
            review()
        def update_choices(*_, reset=False):
            vi_choices.clear()
            vi_choices.update((item.label, item) for item in
                self.service.vietnamese_units(self.state, SectionName(section.get())))
            ja_choices.clear()
            ja_choices.update((item.label, item) for item in
                self.service.japanese_choices(self.state, SectionName(section.get())))
            vi_combo.configure(values=list(vi_choices))
            ja_combo.configure(values=list(ja_choices))
            selected_vi.set("" if reset else next((label for label, item in vi_choices.items()
                if item.text == placement.confirmed_vietnamese_text), ""))
            selected_ja.set("" if reset else next((label for label, item in ja_choices.items()
                if item.sentence_id == placement.sentence_id), ""))
            review()
        section.trace_add("write", lambda *_: update_choices(reset=True))
        selected_vi.trace_add("write", suggest)
        selected_ja.trace_add("write", review)
        update_choices()
        def approve():
            try:
                vi_unit = vi_choices.get(selected_vi.get())
                ja_unit = ja_choices.get(selected_ja.get())
                if not vi_unit or not ja_unit:
                    raise ValueError("Chọn câu Việt và câu Nhật tương ứng.")
                if not messagebox.askyesno("Xác nhận lại", f"Tiếng Việt: {vi_unit.text}\n\n"
                    f"Tiếng Nhật: {ja_unit.text}\n\nXác nhận cặp câu này cho ảnh KEY?", parent=dialog):
                    return
                working = deepcopy(self.state.project)
                KeyPlacementService.confirm_mapping(working, self.state.directory, ja_unit.sentence_id, vi_unit.text)
                KeyPlacementService.retarget_key(working, self.state.directory, placement_id,
                    ja_unit.sentence_id, force=True)
                self.state.project = working
                self.service.save_project(self.state)
                dialog.destroy()
                self._refresh_key_rows()
            except Exception as exc:
                self._fail(str(exc))
        ttk.Button(panel, text="Xác nhận lại KEY", command=approve).grid(row=5, column=1, sticky="e", pady=8)
        panel.columnconfigure(1, weight=1)

    def _choose_intro(self, section: SectionName) -> None:
        if not self.state or not self._save():
            return
        path = filedialog.askopenfilename(title=f"Chọn intro {section.value}", parent=self.root,
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm")])
        if not path:
            return
        try:
            self.service.set_intro(self.state, section, Path(path))
            self.intro_paths[section].set(path)
            self._refresh_summary()
        except Exception as exc:
            self._fail(str(exc))

    def _clear_intro(self, section: SectionName) -> None:
        if not self.state:
            return
        try:
            self.service.set_intro(self.state, section, None)
            self.intro_paths[section].set("Chưa chọn")
            self._refresh_summary()
        except Exception as exc:
            self._fail(str(exc))

    def _set_intro_preference(self, section: SectionName, enabled: bool) -> None:
        if self.state:
            self.state.intro_preferences[section.value] = enabled
            self._dirty()

    def _background(self, action, callback) -> None:
        def task():
            try:
                value = action()
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self._fail(error))
            else:
                self.root.after(0, lambda: callback(value))
        Thread(target=task, daemon=True).start()

    def _refresh_voice(self) -> None:
        from te.project.models import TTSSettings
        current = self.state.project.tts_settings if self.state else TTSSettings(provider="voicevox")
        self.voice_status.set("Đang dò VOICEVOX…")
        def work():
            try:
                return VoicevoxProvider(timeout=3).list_voices(current)
            except Exception as exc:
                raise RuntimeError("Không kết nối được VOICEVOX. Hãy mở Engine tại 127.0.0.1:50021 rồi thử lại.") from exc
        self._background(work, self._voices_loaded)

    def _voices_loaded(self, speakers: list[dict]) -> None:
        self.speakers = speakers
        self.voice_combo.configure(values=[speaker["name"] for speaker in speakers])
        self._speaker_selected()
        self.voice_status.set(f"Đã phát hiện {len(speakers)} giọng.")

    def _speaker_selected(self) -> None:
        speaker = next((speaker for speaker in self.speakers if speaker["name"] == self.voice.get()), None)
        if speaker:
            styles = [style["name"] for style in speaker.get("styles", [])]
            self.style_combo.configure(values=styles)
            if self.style.get() not in styles:
                self.style.set("ノーマル" if "ノーマル" in styles else (styles[0] if styles else ""))
        self._dirty()

    def _voice_sample(self) -> None:
        if not self.state or not self._save():
            return
        settings = deepcopy(self.state.project.tts_settings)
        output = self.state.directory / "generated" / "audio" / "studio_voice_sample.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        self.voice_status.set("Đang tạo mẫu giọng…")
        def work():
            try:
                VoicevoxProvider(timeout=15).synthesize("今日の試合は、忘れられない一戦です。", settings, output)
            except Exception as exc:
                raise RuntimeError("Không tạo được mẫu giọng. Kiểm tra VOICEVOX Engine và kiểu giọng đã chọn.") from exc
            return output
        def done(path):
            self.voice_status.set(f"Đã tạo mẫu: {path.name}")
            try:
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except ImportError:
                messagebox.showinfo("Mẫu giọng", f"File WAV: {path}", parent=self.root)
        self._background(work, done)

    def _refresh_summary(self) -> None:
        if not self.state:
            return
        project = self.state.project
        done = sum(bool(self.state.sections[s.value]["ja"].strip()) for s in SECTIONS)
        keys = sum(item.status == "ready" for item in project.key_placements)
        review = sum(item.status == "manual_review" for item in project.key_placements)
        drafts = len(self.state.key_drafts)
        missing = sum(not asset.available for asset in project.media)
        self.summary.set(f"{done}/4 phần có lời Nhật • {self.service.eligible_image_count(self.state)} ảnh thường • "
                         f"{keys} KEY đã xác nhận, {drafts} chưa xác nhận, {review} cần xem lại • "
                         f"{missing} file media bị thiếu. "
                         f"Dự án: {self.state.directory}")
        self._refresh_compact_summaries()
        self.ai.refresh_assignment_status()

    def _refresh_compact_summaries(self) -> None:
        if not self.state:
            return
        state = self.state
        media = {item.id: item for item in state.project.media}
        keys = [(item.get("section", "P1"), item["media_id"], item.get("duration_seconds"))
                for item in state.key_drafts]
        for item in state.project.key_placements:
            unit = next((u for u in state.project.sentence_units if u.id == item.sentence_id), None)
            segment = next((s for s in state.project.segments if unit and s.id == unit.segment_id), None)
            keys.append((segment.section.value if segment else "?", item.media_id,
                         item.duration_ms / 1000 if item.duration_ms is not None else None))
        brief = [f"{section} {media[media_id].filename[:23]} {self._key_duration_label(duration)}"
                 for section, media_id, duration in keys[:2]
                 if media_id in media]
        self.key_summary.set(f"{len(keys)} ảnh KEY" + (" • " + "; ".join(brief) if brief else ""))
        self.ordinary_summary.set(f"{self.service.eligible_image_count(state)} ảnh • "
                                  f"{self.min_duration.get()}–{self.max_duration.get()}s • "
                                  f"{Path(state.project.media_library_path).name[:25] if state.project.media_library_path else 'Chưa chọn thư mục'}")
        self.intro_summary.set(" | ".join(f"{section.value} {'✓' if self.service.intro_path(state, section) else '—'}"
                                          for section in SECTIONS))
        if state.narration_mode == "multi":
            try:
                plan, warnings = speech_plan(state)
                roles = sorted({role for parts in plan.values() for role, content in parts if content.strip()})
                missing = [role for role in roles if role != "A" and role not in state.speaker_data["voices"]]
                status = ("Thiếu giọng " + ", ".join(missing) if missing else "Đã sẵn sàng")
                if warnings:
                    status += " • Có đoạn dùng giọng A mặc định"
                self.voice_summary.set(f"Đa giọng: {'/'.join(roles)} — {status}")
            except ValueError:
                self.voice_summary.set("Đa giọng: Cần xác nhận phân vai")
        else:
            self.voice_summary.set(f"Một giọng • {self.voice.get()} • {self.style.get() or 'mặc định'} • {self.speed.get()}×")
        self.subtitle_summary.set(f"{self.subtitle_font.get()} • {self.subtitle_size.get()}px • "
                                  f"{self.subtitle_color.get()}")
        self.output_summary.set(f"{self.motion.get()} • {self.transition.get()}")

    @staticmethod
    def _key_duration_label(duration: object) -> str:
        if duration in (None, ""):
            return "mặc định"
        try:
            return f"{float(duration):g} s"
        except (TypeError, ValueError):
            return "thời lượng chưa hợp lệ"

    def _append_render_log(self, message: str) -> None:
        self.render_status.set(message)
        self.render_log.configure(state="normal")
        self.render_log.insert("end", message + "\n")
        self.render_log.see("end")
        self.render_log.configure(state="disabled")

    def _latest_performance_report(self) -> Path | None:
        if not self.state:
            return None
        folder = self.state.directory / "generated" / "output" / "diagnostics"
        return next(iter(sorted(folder.glob("performance-*.json"), reverse=True)), None)

    def _refresh_performance_summary(self) -> None:
        path = self._latest_performance_report()
        if path is None:
            self.performance_summary.set("Thời gian dựng: chưa có báo cáo.")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            total = data["total_seconds"]
            label = "Preview" if data["render_type"] == "preview" else "Full HD"
            status = {"success": "hoàn tất", "failed": "lỗi", "cancelled": "đã hủy"}.get(
                data["status"], data["status"])
            self.performance_summary.set(
                f"Thời gian dựng: {label} • {total:.1f}s • {status} • "
                f"WAV mới {data['counts']['new_wavs']}, dùng cache {data['counts']['wav_cache_hits']}")
        except (OSError, ValueError, KeyError, TypeError):
            self.performance_summary.set("Thời gian dựng: báo cáo chưa đọc được.")

    def _open_performance_report(self) -> None:
        path = self._latest_performance_report()
        if path:
            self._open_path(path)
        else:
            self._fail("Dự án chưa có báo cáo thời gian dựng.")

    def _set_job_busy(self, busy: bool) -> None:
        if busy:
            self._disabled_widgets.clear()
            def visit(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton, tk.Text)):
                        state = str(widget.cget("state"))
                        self._disabled_widgets.append((widget, state))
                        widget.configure(state="disabled")
                    visit(widget)
            visit(self.body)
            for dialog in self._details.values():
                visit(dialog)
        else:
            for widget, state in self._disabled_widgets:
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass
            self._disabled_widgets.clear()
        self._job_running = busy

    def _start_job(self, label: str, action) -> None:
        if self._job_running:
            self._append_render_log("Đang xử lý video này; vui lòng đợi thao tác hiện tại.")
            return
        if not self.state or not self._save():
            return
        if self.state.narration_mode == "multi":
            try:
                plan, warnings = speech_plan(self.state)
                roles = {role for parts in plan.values() for role, content in parts if content.strip()}
                missing = sorted(role for role in roles if role != "A" and
                                 role not in self.state.speaker_data["voices"])
                if missing:
                    raise ValueError("Thiếu giọng " + ", ".join(missing) + ".")
                if warnings:
                    messagebox.showwarning("Đoạn Nhật chưa gán vai",
                        "\n".join(warnings) + "\nCác đoạn này sẽ dùng giọng A.", parent=self.root)
            except ValueError as exc:
                choice = messagebox.askyesnocancel("Đa giọng chưa sẵn sàng",
                    f"{exc}\n\nCó: mở AI phân vai. Không: dùng Một giọng. Hủy: dừng thao tác.", parent=self.root)
                if choice is None:
                    return
                if choice:
                    self.ai.open_review()
                    return
                self.narration_mode.set("Một giọng")
                if not self._save():
                    return
        self._set_job_busy(True)
        self._append_render_log(label)
        state = self.state
        def worker():
            try:
                result = action(state, lambda message: self._job_queue.put(("progress", message)))
            except Exception as exc:
                self._job_queue.put(("error", exc))
            else:
                self._job_queue.put(("done", result))
        Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_job)

    def _poll_job(self) -> None:
        finished = False
        while True:
            try:
                kind, value = self._job_queue.get_nowait()
            except Empty:
                break
            if kind == "progress":
                self._append_render_log(str(value))
            elif kind == "done":
                self._append_render_log(f"Hoàn tất: {value}")
                self._refresh_summary()
                self._refresh_performance_summary()
                if (isinstance(value, Path) and self.state and
                        self.state.last_final == str(value) and
                        messagebox.askyesno("Video đã hoàn tất", f"Đã xuất video:\n{value}\n\nMở video ngay?",
                                            parent=self.root)):
                    self._open_path(value)
                finished = True
            elif kind == "error":
                message = self._production_error(value)
                self._append_render_log("Lỗi: " + message)
                self._fail(message)
                self._refresh_performance_summary()
                finished = True
        if finished:
            self._set_job_busy(False)
        elif self._job_running:
            self.root.after(100, self._poll_job)

    @staticmethod
    def _production_error(error: Exception) -> str:
        if isinstance(error, RenderError):
            return (f"FFmpeg không hoàn tất bước {error.stage}. "
                    "Xem log trong generated/cache/render của dự án.")
        value = str(error)
        if value.startswith(("Giọng hoặc style của vai", "Thiếu giọng ",
                             "Phân vai đã cũ", "Span tiếng Nhật", "Đoạn phân vai")):
            return value
        translations = {
            "KEY image placements overlap": "Hai ảnh KEY chồng thời gian; hãy sửa thời lượng hoặc độ lệch KEY.",
            "KEY offset must keep reveal": "Độ lệch KEY đưa ảnh ra ngoài đoạn lời dẫn; hãy sửa lại.",
            "Japanese font": "Không tìm thấy phông Nhật; hãy chọn file phông trong mục giọng/phụ đề.",
            "FFmpeg and ffprobe": "Cần cài FFmpeg và ffprobe trong PATH để xuất video.",
            "VOICEVOX": "VOICEVOX chưa sẵn sàng hoặc giọng/kiểu giọng không hợp lệ. Hãy kiểm tra Engine rồi thử lại.",
        }
        return next((message for trigger, message in translations.items() if trigger in value), value)

    def _generate_narration(self) -> None:
        self._start_job("Đang tạo/cập nhật lời đọc Nhật…", self.renderer.generate_narration)

    def _render_preview(self) -> None:
        if self.queue.is_active():
            self._fail("Hàng đợi Render đang chạy; hãy chờ hoặc tạm dừng trước khi render thủ công.")
            return
        self._start_job("Đang chuẩn bị bản xem nhanh…",
            lambda state, progress: self.renderer.render_video(state, preview=True, progress=progress))

    def _render_full(self) -> None:
        self._enqueue_render(auto_start=True)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            self._fail(f"Không tìm thấy: {path}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except OSError as exc:
            self._fail(f"Không mở được {path}: {exc}")

    def _open_preview(self) -> None:
        if self.state:
            self._open_path(self.state.directory / "generated" / "output" / "preview.mp4")

    def _open_final(self) -> None:
        if self.state and self.state.last_final:
            self._open_path(Path(self.state.last_final))
        else:
            self._fail("Dự án chưa có video Full HD đã xuất.")

    def _open_output_folder(self) -> None:
        folder = Path(self.service.settings["output_root"])
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

    def _close(self) -> None:
        if self._queue_pending:
            self._save_status.set("Đang tạo snapshot hàng đợi; hãy đợi lưu xong trước khi đóng.")
            return
        if self._job_running:
            self._append_render_log("Hãy đợi thao tác hiện tại hoàn tất trước khi đóng TE Studio.")
            return
        if self.state and not self._save():
            if not messagebox.askyesno("Thoát TE Studio", "Có thay đổi chưa lưu. Vẫn thoát?", parent=self.root):
                return
        if self.queue.is_active():
            if not messagebox.askyesno("Hàng đợi Render đang chạy",
                    "Hủy tác vụ hiện tại và đóng TE Studio? Các tác vụ chưa chạy vẫn được giữ.", parent=self.root):
                return
            self.queue.pause_after_current()
            self.queue.cancel_current()
            self._close_after_queue()
            return
        self.root.destroy()

    def _close_after_queue(self) -> None:
        if self.queue.is_active():
            self.root.after(200, self._close_after_queue)
        else:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    StudioApp(root)
    root.mainloop()
