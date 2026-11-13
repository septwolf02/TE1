"""Tkinter editor and local video production controls with Vietnamese labels."""
from __future__ import annotations

from copy import deepcopy
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


SECTIONS = tuple(SectionName)
MOTION_LABELS = {
    "Tự động: zoom và pan": "mixed", "Chỉ zoom vào/ra": "zoom",
    "Chỉ pan": "pan", "Không chuyển động": "none",
}
TRANSITION_LABELS = {"Cắt thẳng": "cut", "Mờ dần": "fade", "Chuyển mờ": "crossfade"}


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
        self.style = tk.StringVar()
        self.speed = tk.StringVar(value="1.0")
        self.pitch = tk.StringVar(value="0.0")
        self.intonation = tk.StringVar(value="1.0")
        self.volume = tk.StringVar(value="1.0")
        self.subtitle_font = tk.StringVar(value="bold_japanese")
        self.subtitle_color = tk.StringVar(value="#FFFFFF")
        self.subtitle_chars = tk.StringVar(value="24")
        self.subtitle_size = tk.StringVar(value="156")
        self.font_path = tk.StringVar(value="")
        self.motion = tk.StringVar(value="Tự động: zoom và pan")
        self.transition = tk.StringVar(value="Cắt thẳng")
        self.summary = tk.StringVar(value="Tạo hoặc mở dự án để bắt đầu.")
        self.section_status = tk.StringVar()
        self.render_status = tk.StringVar(value="Sẵn sàng tạo lời đọc hoặc render.")
        self._job_queue: Queue[tuple[str, object]] = Queue()
        self._job_running = False
        self._disabled_widgets: list[tuple[object, str]] = []
        self.renderer = StudioRenderService(self.service)
        self.ai = SpeakerAIController(self)
        self.intro_paths = {section: tk.StringVar() for section in SECTIONS}
        self.root.title("TE Studio — Trình biên tập")
        self.root.geometry("1200x900")
        self.root.minsize(920, 690)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._choose_initial_project()

    def _build(self) -> None:
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=14)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        self.body = body
        self._build_header()
        self._build_script()
        self.ai.build()
        self._build_keys()
        self._build_media()
        self._build_intros()
        self._build_voice()
        self._build_motion()
        self._build_render()
        self._build_footer()

    def _group(self, title: str) -> ttk.LabelFrame:
        group = ttk.LabelFrame(self.body, text=title, padding=10)
        group.pack(fill="x", pady=6)
        return group

    def _build_header(self) -> None:
        box = self._group("1. Tên video và kênh")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Tên video *").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Entry(box, textvariable=self.video_name).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(box, text="Kênh").grid(row=0, column=2, sticky="w", padx=4)
        self.channel_combo = ttk.Combobox(box, textvariable=self.channel, state="readonly", width=23)
        self.channel_combo.grid(row=0, column=3, sticky="ew", padx=4)
        self.channel_combo.bind("<<ComboboxSelected>>", lambda _e: self._channel_selected())
        actions = ttk.Frame(box)
        actions.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for label, command in (("Tạo dự án", self._new_project), ("Mở dự án", self._open_project),
                               ("Lưu", self._save_clicked), ("Đổi tên", self._rename_project),
                               ("Nhân bản", self._duplicate_project),
                               ("Tạo kênh", self._create_channel), ("Đổi tên kênh", self._rename_channel),
                               ("Áp dụng mặc định kênh", self._apply_channel),
                               ("Lưu cài đặt vào kênh", self._save_channel),
                               ("Thư mục mặc định", self._configure_roots)):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(box, textvariable=self._save_status).grid(row=2, column=0, columnspan=4,
                                                             sticky="w", pady=(7, 0))
        self.video_name.trace_add("write", lambda *_: self._dirty())
        self._refresh_channels()

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
        ttk.Label(tabs, textvariable=self.section_status).pack(side="right")
        panel = ttk.Frame(box)
        panel.pack(fill="both", expand=True, pady=6)
        panel.columnconfigure((0, 1), weight=1)
        ttk.Label(panel, text="Bản tham chiếu tiếng Việt (giữ nguyên đoạn văn)").grid(row=0, column=0, sticky="w")
        ttk.Label(panel, text="Lời dẫn tiếng Nhật gửi tới VOICEVOX").grid(row=0, column=1, sticky="w")
        self.vn_text = tk.Text(panel, height=12, wrap="word", undo=True, font=("Segoe UI", 11))
        self.jp_text = tk.Text(panel, height=12, wrap="word", undo=True, font=("Yu Gothic", 11))
        self.vn_text.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.jp_text.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        for widget in (self.vn_text, self.jp_text):
            widget.bind("<KeyRelease>", lambda _e: self._script_changed())
            widget.bind("<FocusOut>", lambda _e: self._capture_section())
            widget.bind("<MouseWheel>", lambda event, w=widget:
                (w.yview_scroll(-int(event.delta / 120), "units"), "break")[1])
        ttk.Label(box, text="Chuyển P1–P4 không làm mất nội dung; TE không tự dịch hoặc viết lại kịch bản.").pack(anchor="w")

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
        self.key_rows = ttk.Frame(box)
        self.key_rows.pack(fill="x")

    def _build_media(self) -> None:
        box = self._group("4. Thư mục ảnh thường của video này")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="Chọn thư mục…", command=self._choose_media_folder).pack(side="left")
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
        box = self._group("6. Giọng VOICEVOX và phụ đề")
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
        for label, variable in (("Tốc độ", self.speed), ("Cao độ", self.pitch),
                                ("Ngữ điệu", self.intonation), ("Âm lượng", self.volume)):
            ttk.Label(row, text=label).pack(side="left", padx=(8, 0))
            ttk.Entry(row, textvariable=variable, width=6).pack(side="left", padx=3)
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
        for variable in (self.voice, self.style, self.speed, self.pitch, self.intonation,
                         self.volume, self.subtitle_font, self.subtitle_color, self.subtitle_chars,
                         self.subtitle_size,
                         self.font_path):
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

    def _build_footer(self) -> None:
        box = self._group("9. Lưu và tình trạng dự án")
        ttk.Label(box, textvariable=self.summary, wraplength=1050).pack(anchor="w")
        ttk.Button(box, text="Lưu dự án ngay", command=self._save_clicked).pack(anchor="w", pady=5)
        future = ttk.LabelFrame(self.body, text="Hàng đợi render — dự kiến Giai đoạn 3", padding=10)
        future.pack(fill="x", pady=6)
        ttk.Button(future, text="Thêm vào hàng đợi render (chưa khả dụng)", state="disabled").pack(anchor="w")

    def _build_render(self) -> None:
        box = self._group("8. Tạo lời đọc, xem nhanh và xuất video")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=3)
        for label, action in (("Tạo / cập nhật lời đọc Nhật", self._generate_narration),
                              ("Xem nhanh 960×540", self._render_preview),
                              ("Xuất Full HD 1920×1080", self._render_full)):
            ttk.Button(row, text=label, command=action).pack(side="left", padx=4)
        files = ttk.Frame(box)
        files.pack(fill="x", pady=3)
        for label, action in (("Mở bản xem nhanh", self._open_preview),
                              ("Mở video cuối", self._open_final),
                              ("Mở thư mục xuất", self._open_output_folder)):
            ttk.Button(files, text=label, command=action).pack(side="left", padx=4)
        ttk.Label(box, textvariable=self.render_status, wraplength=1050).pack(anchor="w", pady=3)
        self.render_log = tk.Text(box, height=5, state="disabled", wrap="word")
        self.render_log.pack(fill="x")

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
        settings = state.project.tts_settings
        self.voice.set(settings.voice_id)
        self.style.set(settings.style or "")
        self.speed.set(str(settings.speaking_rate))
        self.pitch.set(str(settings.pitch))
        self.intonation.set(str(settings.intonation))
        self.volume.set(str(settings.volume))
        self.subtitle_font.set(state.project.subtitle_style.get("font", "bold_japanese"))
        self.subtitle_color.set(state.project.subtitle_style.get("text_color", "#FFFFFF"))
        self.subtitle_chars.set(str(state.project.subtitle_style.get("max_chars_per_line", 24)))
        self.subtitle_size.set(str(state.subtitle_size))
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
        state.image_min_seconds = float(self.min_duration.get())
        state.image_max_seconds = float(self.max_duration.get())
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

    def _choose_media_folder(self) -> None:
        state = self.state
        if not state or not self._save():
            return
        path = filedialog.askdirectory(title="Chọn thư mục ảnh thường cho video này", parent=self.root)
        if not path:
            return
        snapshot = deepcopy(state)
        self.media_count.set("Đang quét thư mục ảnh…")
        def work():
            return self.service.select_media_folder(snapshot, Path(path))
        def done(count):
            if self.state is state:
                state.project = snapshot.project
                self.media_path.set(path)
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
            panel = ttk.Frame(self.key_rows, padding=5, relief="groove")
            panel.pack(fill="x", pady=4)
            photo = self._thumbnail(Path(asset.path)) if asset.available else None
            if photo:
                ttk.Label(panel, image=photo).grid(row=0, column=0, rowspan=3, padx=5)
            ttk.Label(panel, text=asset.filename + (" — thiếu file" if not asset.available else ""),
                      wraplength=200).grid(row=0, column=1, sticky="w")
            if kind == "draft":
                duration, offset, identifier, status = self._render_key_draft(panel, item)
            else:
                unit = next((u for u in self.state.project.sentence_units if u.id == item.sentence_id), None)
                segment = next((s for s in self.state.project.segments if unit and s.id == unit.segment_id), None)
                ttk.Label(panel, text=segment.section.value if segment else "Cần xem lại").grid(row=0, column=2)
                ttk.Label(panel, text=unit.synthesis_text if unit else "Câu Nhật đã mất",
                          wraplength=350).grid(row=0, column=3, sticky="w")
                ttk.Label(panel, text=item.confirmed_vietnamese_text,
                          wraplength=350).grid(row=1, column=1, columnspan=3, sticky="w")
                duration = tk.StringVar(value="" if item.duration_ms is None else str(item.duration_ms / 1000))
                offset = tk.StringVar(value=str(item.offset_ms))
                identifier = item.id
                status = ("Cần xem lại thủ công" if item.status == "manual_review" else
                          "Đã xác nhận • khóa KEY" if item.locked else "Đã xác nhận")
                def update(placement=item, dur=duration, off=offset):
                    try:
                        KeyPlacementService.set_timing(self.state.project, self.state.directory,
                            placement.id, offset_ms=int(off.get()),
                            duration_ms=round(float(dur.get()) * 1000) if dur.get() else None)
                        self.service.save_project(self.state)
                        self._refresh_key_rows()
                    except Exception as exc:
                        self._fail(str(exc))
                ttk.Button(panel, text="Lưu thời điểm", command=update).grid(row=2, column=3, sticky="e")
                ttk.Button(panel, text="Chọn lại câu", command=lambda i=item.id: self._retarget_key(i)).grid(
                    row=0, column=4, sticky="e")
            timing = ttk.Frame(panel)
            timing.grid(row=2, column=1, columnspan=2, sticky="w")
            ttk.Label(timing, text="Thời lượng (giây)").pack(side="left")
            ttk.Entry(timing, textvariable=duration, width=7).pack(side="left", padx=4)
            ttk.Label(timing, text="Lệch (ms)").pack(side="left")
            ttk.Entry(timing, textvariable=offset, width=7).pack(side="left", padx=4)
            ttk.Label(panel, text=status).grid(row=1, column=4, padx=5)
            ttk.Button(panel, text="Xóa KEY", command=lambda i=identifier: self._remove_key(i)).grid(row=2, column=4)
            panel.columnconfigure(3, weight=1)

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

    def _append_render_log(self, message: str) -> None:
        self.render_status.set(message)
        self.render_log.configure(state="normal")
        self.render_log.insert("end", message + "\n")
        self.render_log.see("end")
        self.render_log.configure(state="disabled")

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
        self._start_job("Đang chuẩn bị bản xem nhanh…",
            lambda state, progress: self.renderer.render_video(state, preview=True, progress=progress))

    def _render_full(self) -> None:
        self._start_job("Đang chuẩn bị bản Full HD…",
            lambda state, progress: self.renderer.render_video(state, preview=False, progress=progress))

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
        if self._job_running:
            self._append_render_log("Hãy đợi thao tác hiện tại hoàn tất trước khi đóng TE Studio.")
            return
        if self.state and not self._save():
            if not messagebox.askyesno("Thoát TE Studio", "Có thay đổi chưa lưu. Vẫn thoát?", parent=self.root):
                return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    StudioApp(root)
    root.mainloop()
