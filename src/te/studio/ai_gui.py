"""Tkinter AI settings and Vietnamese-first speaker review; no render integration."""
from __future__ import annotations

from copy import deepcopy
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path
import time

from te.project.models import SectionName
from te.studio.ai_auth import ChatGPTAuth, CredentialStore
from te.studio.ai_clients import OpenAIConnection
from te.studio.ai_speakers import (ROLES, filter_assignments, passage, script_fingerprint,
                                   section_counts, validate_speaker_data, voice_key, voice_label)
from te.tts import VoicevoxProvider


class SpeakerAIController:
    def __init__(self, app):
        self.app = app
        self.credentials = CredentialStore()
        prefs = app.service.settings.setdefault("ai", {"provider": "chatgpt", "models": {},
                                                         "reasoning": "Nhanh"})
        self.provider = tk.StringVar(value=prefs.get("provider", "chatgpt"))
        self.model = tk.StringVar()
        self.reasoning = tk.StringVar(value=prefs.get("reasoning", "Nhanh"))
        self.api_key = tk.StringVar()
        self.status = tk.StringVar(value="Chưa kết nối AI; vẫn có thể phân vai thủ công.")
        self.assignment_status = tk.StringVar(value="Chưa phân vai")
        self.available: dict[str, list[dict]] = {"chatgpt": [], "api": []}

    def _prefs(self):
        return self.app.service.settings["ai"]

    def _auth(self):
        prefs = self._prefs()
        if not prefs.get("host_id"):
            prefs["host_id"] = ChatGPTAuth.new_host_id()
            self.app.service.save_settings()
        return ChatGPTAuth(self.credentials, prefs["host_id"])

    def _connection(self, provider=None):
        provider = provider or self.provider.get()
        if provider == "chatgpt":
            return OpenAIConnection("chatgpt", self._auth().access_token)
        return OpenAIConnection("api", lambda: self.credentials.get("api_key") or self._no_api_key())

    @staticmethod
    def _no_api_key():
        raise PermissionError("Chưa lưu OpenAI API key.")

    def build(self):
        box = self.app._group("AI phân vai")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Kết nối").pack(side="left")
        ttk.Combobox(row, textvariable=self.provider, values=("chatgpt", "api"),
                     state="readonly", width=14).pack(side="left", padx=5)
        ttk.Label(row, text="Model").pack(side="left", padx=(10, 0))
        self.model_combo = ttk.Combobox(row, textvariable=self.model, state="readonly", width=30)
        self.model_combo.pack(side="left", padx=5)
        self.model_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_model())
        ttk.Label(row, text="Reasoning").pack(side="left", padx=(10, 2))
        ttk.Combobox(row, textvariable=self.reasoning, values=("Nhanh", "Vừa"),
                     state="readonly", width=10).pack(side="left")
        self.reasoning.trace_add("write", lambda *_: self._save_reasoning())
        ttk.Label(row, textvariable=self.assignment_status).pack(side="left", padx=10)
        ttk.Button(row, text="AI phân vai", command=lambda: self.open_review(auto_analyze=True)).pack(
            side="right", padx=4)
        ttk.Button(row, text="Cài đặt AI", command=lambda: self.app._show_detail("ai")).pack(
            side="right", padx=4)
        ttk.Label(box, textvariable=self.status).pack(anchor="w")
        self.app._create_detail("ai", "Cài đặt AI", self._build_settings)
        self._provider_changed()

    def _build_settings(self):
        box = self.app._group("Kết nối AI")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=3)
        ttk.Button(row, text="Continue with ChatGPT", command=self._connect_chatgpt).pack(side="left", padx=3)
        ttk.Button(row, text="Reconnect", command=lambda: self._connect_chatgpt(consent=True)).pack(side="left", padx=3)
        ttk.Button(row, text="Disconnect", command=self._disconnect).pack(side="left", padx=3)
        self.provider.trace_add("write", lambda *_: self._provider_changed())
        api = ttk.Frame(box)
        api.pack(fill="x", pady=3)
        ttk.Label(api, text="OpenAI API key").pack(side="left")
        ttk.Entry(api, textvariable=self.api_key, show="*", width=32).pack(side="left", padx=5)
        ttk.Button(api, text="Save securely", command=self._save_api_key).pack(side="left")
        ttk.Button(api, text="Test connection", command=self._test_api).pack(side="left", padx=5)
        ttk.Label(api, text="API có thể phát sinh phí riêng; chỉ gọi khi bạn bấm phân vai.").pack(side="left")
        ttk.Button(box, text="Tải models cho kết nối đang chọn", command=self._load_models).pack(anchor="w", pady=5)
        ttk.Label(box, textvariable=self.status).pack(anchor="w")
        ttk.Label(box, text="Giọng A/B/C/D chỉ lưu cho B2; render hiện tại vẫn dùng một giọng VOICEVOX.").pack(anchor="w")

    def _provider_changed(self):
        provider = self.provider.get()
        if provider not in ("chatgpt", "api"):
            return
        self._prefs()["provider"] = provider
        self.app.service.save_settings()
        choices = self.available[provider]
        self.model_combo.configure(values=[item["id"] for item in choices])
        saved = self._prefs().setdefault("models", {}).get(provider, "")
        self.model.set(saved if saved in [item["id"] for item in choices] else "")
        try:
            if provider == "chatgpt":
                record = self._auth().record()
                label = ("Connected: " + record.get("email", "ChatGPT") if record and
                         "chatgpt.tokens.use.direct" in record.get("scopes", []) else
                         "Permission required" if record else "Disconnected")
            else:
                label = "API key đã lưu" if self.credentials.get("api_key") else "API key chưa lưu"
        except Exception as exc:
            label = str(exc)
        self.status.set(label)

    def _save_model(self):
        self._prefs().setdefault("models", {})[self.provider.get()] = self.model.get()
        self.app.service.save_settings()

    def _save_reasoning(self):
        if self.reasoning.get() in ("Nhanh", "Vừa"):
            self._prefs()["reasoning"] = self.reasoning.get()
            self.app.service.save_settings()

    def _models_loaded(self, models, provider):
        self.available[provider] = models
        if provider == self.provider.get():
            self.model_combo.configure(values=[item["id"] for item in models])
            saved = self._prefs().setdefault("models", {}).get(provider)
            ids = [item["id"] for item in models]
            self.model.set(saved if saved in ids else next((item for item in ids if "luna" in item), ids[0]))
            self._save_model()
            selected = next(item for item in models if item["id"] == self.model.get())
            reason = "Reasoning hỗ trợ: " + ", ".join(selected["reasoning"]) if selected["reasoning"] else (
                "Reasoning chưa được catalog xác nhận; sẽ bỏ tham số này.")
            self.status.set(f"Đã tải {len(models)} models. {reason}")

    def _load_models(self):
        provider = self.provider.get()
        self.status.set("Đang tải danh sách models…")
        self.app._background(lambda: self._connection(provider).models(),
                             lambda items: self._models_loaded(items, provider))

    def _connect_chatgpt(self, *, consent=False):
        self.provider.set("chatgpt")
        self.status.set("Đang chờ ChatGPT trong trình duyệt…")
        def done(record):
            self.status.set("Connected: " + record.get("email", "ChatGPT") if
                            "chatgpt.tokens.use.direct" in record["scopes"] else "Permission required")
            if "chatgpt.tokens.use.direct" in record["scopes"]:
                self._load_models()
        self.app._background(lambda: self._auth().connect(consent=consent), done)

    def _disconnect(self):
        provider = self.provider.get()
        if provider == "api":
            try:
                self.credentials.delete("api_key")
                self.available["api"] = []
                self._provider_changed()
            except Exception as exc:
                self.app._fail(str(exc))
            return
        self.app._background(lambda: self._auth().disconnect(),
                             lambda revoked: self.status.set("Disconnected" if revoked else
                                "Đã xóa token cục bộ; chưa xác nhận thu hồi trên server."))

    def _save_api_key(self):
        value = self.api_key.get().strip()
        if not value:
            self.app._fail("Hãy nhập API key.")
            return
        try:
            self.credentials.set("api_key", value)
            self.api_key.set("")
            self.status.set("API key đã lưu trong Windows Credential Manager.")
        except Exception as exc:
            self.app._fail(str(exc))

    def _test_api(self):
        self.provider.set("api")
        self.status.set("Đang kiểm tra API key bằng model list; chưa gọi inference…")
        self.app._background(lambda: self._connection("api").models(),
                             lambda items: self._models_loaded(items, "api"))

    def project_loaded(self):
        if self.app.state:
            self.refresh_assignment_status()
            status = self.app.state.speaker_data["status"]
            if status == "stale":
                self.status.set("Phân vai cũ đã stale do script đổi; hãy xem lại trước khi xác nhận.")

    def refresh_assignment_status(self):
        if self.app.state:
            data = self.app.state.speaker_data
            self.assignment_status.set(f"{len(data['assignments'])} đoạn • {data['status']}")

    def _ready_for_analysis(self) -> bool:
        provider = self.provider.get()
        model = self.model.get()
        if not model or not any(item["id"] == model for item in self.available[provider]):
            return False
        try:
            if provider == "api":
                return bool(self.credentials.get("api_key"))
            record = self._auth().record()
            return bool(record and "chatgpt.tokens.use.direct" in record.get("scopes", [])
                        and "resource.invoke" in record.get("scopes", [])
                        and (record.get("expires_at", 0) > time.time() or record.get("refresh_token")))
        except Exception:
            return False

    def open_review(self, *, auto_analyze=False):
        app = self.app
        if not app.state or not app._save():
            return
        state = app.state
        dialog = tk.Toplevel(app.root)
        dialog.title("AI phân vai — xem tiếng Việt trước")
        dialog.geometry("1250x850")
        dialog.transient(app.root)
        dialog.grab_set()
        voice_table = ttk.LabelFrame(dialog, text="Giọng theo vai (lưu cho B2)", padding=8)
        voice_table.pack(fill="x", padx=8, pady=5)
        for column, title in enumerate(("Vai", "Tên nhân vật", "Giọng đã lưu", "Chọn giọng VOICEVOX",
                                        "Nghe thử", "Lưu", "Trạng thái")):
            ttk.Label(voice_table, text=title).grid(row=0, column=column, sticky="w", padx=4)
        voice_rows = {}
        voice_choices: dict[str, dict] = {}
        for number, role_id in enumerate(ROLES, 1):
            saved_voice = state.speaker_data["voices"].get(role_id)
            name = tk.StringVar(value=state.speaker_data["identities"].get(role_id, ROLES[role_id]))
            selected_voice = tk.StringVar(value=voice_label(saved_voice) if saved_voice else "")
            saved_text = tk.StringVar(value=voice_label(saved_voice))
            status = tk.StringVar(value="Đã lưu" if saved_voice else "Đã lưu • chưa chọn giọng")
            ttk.Label(voice_table, text=f"{role_id} — {ROLES[role_id]}").grid(
                row=number, column=0, sticky="w", padx=4, pady=3)
            ttk.Entry(voice_table, textvariable=name, width=18).grid(row=number, column=1, sticky="ew", padx=4)
            ttk.Label(voice_table, textvariable=saved_text, width=28).grid(row=number, column=2,
                                                                            sticky="w", padx=4)
            combo = ttk.Combobox(voice_table, textvariable=selected_voice, state="readonly", width=29)
            combo.grid(row=number, column=3, sticky="ew", padx=4)
            ttk.Button(voice_table, text="Nghe thử", command=lambda r=role_id: preview_voice(r)).grid(
                row=number, column=4, padx=4)
            ttk.Button(voice_table, text="Lưu hàng", command=lambda r=role_id: save_voice(r)).grid(
                row=number, column=5, padx=4)
            ttk.Label(voice_table, textvariable=status, width=19).grid(row=number, column=6, sticky="w", padx=4)
            voice_rows[role_id] = {"name": name, "selected": selected_voice, "saved": saved_text,
                                   "status": status, "combo": combo}
        voice_table.columnconfigure(2, weight=1)
        voice_table.columnconfigure(3, weight=1)
        section = tk.StringVar(value="P1")
        result_filter = tk.StringVar(value="Toàn bài")
        review_only = tk.BooleanVar(value=False)
        row = ttk.Frame(dialog, padding=8)
        row.pack(fill="x")
        ttk.Label(row, text="Phần để chọn text thủ công").pack(side="left")
        ttk.Combobox(row, textvariable=section, values=[s.value for s in SectionName],
                     state="readonly", width=6).pack(side="left", padx=5)
        ttk.Label(row, text="Lọc kết quả").pack(side="left", padx=(20, 4))
        ttk.Combobox(row, textvariable=result_filter, values=("Toàn bài", "P1", "P2", "P3", "P4"),
                     state="readonly", width=10).pack(side="left")
        ttk.Checkbutton(row, text="Chỉ cần xem lại", variable=review_only).pack(side="left", padx=8)
        text_frame = ttk.Frame(dialog, padding=8)
        text_frame.pack(fill="both", expand=True)
        vi_box = tk.Text(text_frame, wrap="word", height=12)
        ja_box = tk.Text(text_frame, wrap="word", height=12)
        vi_box.pack(side="left", fill="both", expand=True, padx=4)
        ja_box.pack(side="left", fill="both", expand=True, padx=4)
        def load_section(*_):
            for widget, language in ((vi_box, "vi"), (ja_box, "ja")):
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("1.0", state.sections[section.get()][language])
                widget.configure(state="disabled")
        section.trace_add("write", load_section)
        load_section()
        ttk.Label(dialog, text="Chọn đoạn Việt và đoạn Nhật tương ứng để thêm/sửa thủ công. "
                  "Đoạn chưa rõ có thể để trống vai hoặc đoạn Nhật.").pack(anchor="w", padx=10)
        columns = ("section", "role", "vi", "ja", "review")
        table = ttk.Treeview(dialog, columns=columns, show="headings", height=10)
        for key, label, width in (("section", "P", 40), ("role", "Vai", 45),
                                  ("vi", "Tiếng Việt", 350), ("ja", "Tiếng Nhật", 350),
                                  ("review", "Trạng thái / cảnh báo", 260)):
            table.heading(key, text=label)
            table.column(key, width=width)
        table.pack(fill="both", expand=True, padx=10)
        passage_role = tk.StringVar(value="A")
        ttk.Label(row, text="Vai của đoạn").pack(side="left", padx=(12, 3))
        ttk.Combobox(row, textvariable=passage_role, values=("A", "B", "C", "D", ""),
                     state="readonly", width=5).pack(side="left")
        note = tk.StringVar(value="Chọn dòng để sửa; chọn text trong hai ô để gắn span.")
        ttk.Label(dialog, text="AI sẽ phân vai toàn bộ các phần có nội dung.").pack(anchor="w", padx=10)
        counts_label = tk.StringVar()
        ttk.Label(dialog, textvariable=counts_label).pack(anchor="w", padx=10)
        ttk.Label(dialog, textvariable=note).pack(anchor="w", padx=10)

        def selected_voice(role_id):
            widgets = voice_rows[role_id]
            label = widgets["selected"].get()
            if label in voice_choices:
                return voice_choices[label]
            saved = state.speaker_data["voices"].get(role_id)
            return saved if saved and label == voice_label(saved) else None

        def refresh_voice_status(role_id):
            widgets = voice_rows[role_id]
            saved_name = state.speaker_data["identities"].get(role_id, ROLES[role_id])
            saved_voice = state.speaker_data["voices"].get(role_id)
            widgets["saved"].set(voice_label(saved_voice))
            selected = selected_voice(role_id)
            changed = (widgets["name"].get() != saved_name or
                       voice_key(selected) != voice_key(saved_voice))
            widgets["status"].set("Chưa lưu" if changed else
                                  "Đã lưu" if saved_voice else "Đã lưu • chưa chọn giọng")

        for role_id, widgets in voice_rows.items():
            widgets["name"].trace_add("write", lambda *_, r=role_id: refresh_voice_status(r))
            widgets["combo"].bind("<<ComboboxSelected>>", lambda _event, r=role_id: refresh_voice_status(r))

        def refresh():
            table.delete(*table.get_children())
            all_rows = state.speaker_data["assignments"]
            counts = section_counts(all_rows)
            counts_label.set(f"Tổng {len(all_rows)} đoạn • " + " • ".join(
                f"{key}: {value}" for key, value in counts.items()))
            visible = filter_assignments(all_rows, result_filter.get())
            for item in visible:
                index = next(index for index, original in enumerate(all_rows) if original is item)
                if (review_only.get() and item["confidence"] == "high"
                                       and item["role"] in ROLES and item["japanese"] is not None):
                    continue
                stale = state.speaker_data["status"] == "stale"
                try:
                    vi = passage(state.sections, item, "vi") if not stale else "[script đã đổi]"
                    ja = passage(state.sections, item, "ja") if not stale else "[script đã đổi]"
                except Exception:
                    vi, ja = "[script đã đổi]", "[script đã đổi]"
                table.insert("", "end", iid=str(index), values=(item["section"], item["role"] or "?",
                             vi[:100], ja[:100], "stale" if stale else item["warning"] or
                             ("Đã xác nhận" if item["confirmed"] else "Cần xem lại")))
        refresh()
        result_filter.trace_add("write", lambda *_: refresh())
        review_only.trace_add("write", lambda *_: refresh())
        def select_row(_event):
            selected = table.selection()
            if selected:
                item = state.speaker_data["assignments"][int(selected[0])]
                section.set(item["section"])
                passage_role.set(item["role"] or "")
        table.bind("<<TreeviewSelect>>", select_row)

        def selected_span(widget):
            try:
                start = widget.index("sel.first")
                end = widget.index("sel.last")
            except tk.TclError:
                return None
            def offset(position):
                return len(widget.get("1.0", position))
            return [offset(start), offset(end)]

        def edit_row():
            if state.speaker_data["status"] == "stale":
                note.set("Dữ liệu stale: hãy phân tích lại hoặc chọn Tạo mới thủ công.")
                return
            vi = selected_span(vi_box)
            ja = selected_span(ja_box)
            selected = table.selection()
            if selected:
                index = int(selected[0])
                old = state.speaker_data["assignments"][index]
                item = deepcopy(old)
                if not vi:
                    vi = old["vietnamese"]
                if not ja:
                    ja = old["japanese"]
            else:
                index = None
                if not vi:
                    note.set("Hãy chọn đoạn tiếng Việt trước.")
                    return
                item = {"section": section.get(), "confidence": "review", "warning": "Gán thủ công"}
            item.update(section=section.get(), role=passage_role.get() or None, vietnamese=vi, japanese=ja,
                        source="manual", confirmed=False)
            try:
                from te.studio.ai_speakers import validate_assignments
                item = validate_assignments([item], state.sections)[0]
                item["source"] = "manual"
            except ValueError as exc:
                note.set(str(exc))
                return
            if index is None:
                state.speaker_data["assignments"].append(item)
            else:
                state.speaker_data["assignments"][index] = item
            state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
            state.speaker_data["status"] = "draft"
            refresh()

        analyzing = False
        def analyze():
            nonlocal analyzing
            if analyzing:
                return
            if not self.model.get() or not self.available[self.provider.get()]:
                note.set("Hãy kết nối và tải models trong AI Settings trước.")
                return
            if state.speaker_data["status"] != "stale" and state.speaker_data["assignments"]:
                if not messagebox.askyesno("Phân tích lại", "Gọi AI lại và thay các gợi ý AI cũ? "
                                           "Các sửa thủ công được giữ.", parent=dialog):
                    return
            source = deepcopy(state.sections)
            available_sections = [s.value for s in SectionName if
                                  source[s.value]["vi"].strip() or source[s.value]["ja"].strip()]
            if not available_sections:
                note.set("Chưa có script Việt hoặc Nhật để phân vai.")
                return
            names = {key: widgets["name"].get() for key, widgets in voice_rows.items()}
            provider = self.provider.get()
            settings = {"model": self.model.get(), "reasoning": self.reasoning.get(),
                        "available_models": deepcopy(self.available[provider])}
            analyzing = True
            analyze_button.configure(state="disabled")
            note.set("Đang phân vai toàn bộ các phần có nội dung…")
            def work():
                try:
                    connection = self._connection(provider)
                    items = connection.attribute_speakers(
                        {key: source[key]["vi"] for key in available_sections},
                        {key: source[key]["ja"] for key in available_sections}, names, settings)
                    return items, connection.last_usage, connection.last_diagnostics, None
                except Exception as exc:
                    return None, None, (), str(exc)
            def done(result):
                nonlocal analyzing
                if not dialog.winfo_exists():
                    return
                analyzing = False
                analyze_button.configure(state="normal")
                items, usage, diagnostics, error = result
                if error:
                    note.set("Phân vai thất bại: " + error)
                    return
                if state.sections != source:
                    note.set("Script đã đổi trong lúc AI chạy; kết quả không được áp dụng.")
                    return
                manual = [row for row in state.speaker_data["assignments"] if row["source"] == "manual"
                          and state.speaker_data["status"] != "stale"]
                state.speaker_data["assignments"] = manual + items
                state.speaker_data["identities"] = names
                state.speaker_data["fingerprint"] = script_fingerprint(source)
                state.speaker_data["status"] = "draft"
                app.service.save_project(state)
                self.refresh_assignment_status()
                for role_id in ROLES:
                    refresh_voice_status(role_id)
                refresh()
                tokens = (f" Tokens: {usage.get('total_tokens')}." if usage and
                          usage.get("total_tokens") is not None else "")
                warning = " Cần xem lại: " + "; ".join(diagnostics[:3]) if diagnostics else ""
                note.set(f"Đã nhận {len(items)} đoạn.{tokens}{warning} Kiểm tra và xác nhận thủ công.")
            app._background(work, done)

        def load_voices():
            def done(speakers):
                if not dialog.winfo_exists():
                    return
                voice_choices.clear()
                for speaker in speakers:
                    for style in speaker.get("styles", []):
                        if not isinstance(style.get("id"), int):
                            continue
                        item = {"speaker_uuid": speaker.get("speaker_uuid", ""),
                                "style_id": style["id"], "speaker": speaker["name"],
                                "style": style["name"]}
                        label = voice_label(item)
                        if label in voice_choices:
                            label += f" [{item['speaker_uuid'][-8:]}]"
                        voice_choices[label] = item
                for role_id, widgets in voice_rows.items():
                    saved = state.speaker_data["voices"].get(role_id)
                    current = widgets["selected"].get()
                    matches = [label for label, item in voice_choices.items()
                               if voice_key(item) == voice_key(saved) and saved]
                    if saved and current == voice_label(saved) and matches:
                        widgets["selected"].set(matches[0])
                    values = list(voice_choices)
                    if saved and not matches:
                        values.insert(0, voice_label(saved))
                    widgets["combo"].configure(values=values)
                    refresh_voice_status(role_id)
                note.set(f"Đã tìm thấy {len(voice_choices)} VOICEVOX styles.")
            app._background(lambda: VoicevoxProvider(timeout=3).list_voices(state.project.tts_settings), done)

        def save_voice(role_id):
            widgets = voice_rows[role_id]
            selected = widgets["selected"].get()
            voice = selected_voice(role_id)
            if selected and voice is None:
                note.set("Giọng đã chọn không hợp lệ; hãy tải lại danh sách VOICEVOX.")
                return
            try:
                app.service.save_role_configuration(state, role_id, widgets["name"].get(), voice)
            except Exception as exc:
                note.set(str(exc))
                return
            refresh_voice_status(role_id)
            note.set(f"Đã lưu tên và giọng của vai {role_id}. Render vẫn dùng một giọng đến B2.")

        def preview_voice(role_id):
            voice = selected_voice(role_id)
            if voice is None:
                note.set(f"Vai {role_id} chưa chọn giọng để nghe thử.")
                return
            note.set(f"Đang tạo mẫu giọng {role_id}…")
            def work():
                import winsound
                provider = VoicevoxProvider(timeout=20)
                speakers = provider.list_voices(state.project.tts_settings)
                speaker = next((item for item in speakers if
                    item.get("speaker_uuid") == voice["speaker_uuid"] or
                    item.get("name") == voice.get("speaker")), None)
                style = next((item for item in speaker.get("styles", [])
                              if item.get("id") == voice["style_id"]), None) if speaker else None
                if not speaker or not style:
                    raise ValueError("Giọng hoặc style này không còn trong VOICEVOX Engine.")
                settings = deepcopy(state.project.tts_settings)
                settings.voice_id = speaker.get("speaker_uuid") or speaker["name"]
                settings.style = style["name"]
                with tempfile.TemporaryDirectory(prefix="te-voice-preview-") as folder:
                    path = Path(folder) / "preview.wav"
                    provider.synthesize("こんにちは。声のテストです。", settings, path)
                    winsound.PlaySound(str(path), winsound.SND_FILENAME)
                return role_id
            app._background(work, lambda value: note.set(f"Đã nghe thử giọng {value}."))

        def confirm():
            if state.speaker_data["status"] == "stale":
                note.set("Script đã đổi; hãy phân tích lại hoặc sửa span trước khi xác nhận.")
                return
            rows = state.speaker_data["assignments"]
            if not rows and any(value["vi"].strip() for value in state.sections.values()):
                note.set("Chưa có assignment nào; hãy phân vai trước khi xác nhận.")
                return
            if any(row["role"] not in ROLES or row["japanese"] is None for row in rows):
                note.set("Còn vai hoặc đoạn Nhật chưa rõ; hãy sửa trước khi xác nhận.")
                return
            state.speaker_data["identities"] = {key: widgets["name"].get()
                                                for key, widgets in voice_rows.items()}
            for item in rows:
                item["confirmed"] = True
            state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
            state.speaker_data["status"] = "confirmed"
            try:
                state.speaker_data = validate_speaker_data(state.speaker_data, state.sections)
                app.service.save_project(state)
                self.refresh_assignment_status()
                for role_id in ROLES:
                    refresh_voice_status(role_id)
            except Exception as exc:
                note.set(str(exc))
                return
            refresh()
            note.set("Đã xác nhận và lưu. Render vẫn dùng một giọng cho đến B2.")

        def reset_manual():
            if not messagebox.askyesno("Tạo mới thủ công", "Xóa các assignment cũ của dự án? "
                                       "Tên nhân vật và giọng đã chọn sẽ được giữ.", parent=dialog):
                return
            state.speaker_data["assignments"] = []
            state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
            state.speaker_data["status"] = "draft"
            app.service.save_project(state)
            self.refresh_assignment_status()
            refresh()
            note.set("Đã tạo bản nháp trống. Chọn đoạn Việt/Nhật để gán vai thủ công.")

        buttons = ttk.Frame(dialog, padding=8)
        buttons.pack(fill="x")
        analyze_button = ttk.Button(buttons, text="Phân tích lại bằng AI", command=analyze)
        analyze_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="Thêm / sửa thủ công", command=edit_row).pack(side="left", padx=4)
        ttk.Button(buttons, text="Tạo mới thủ công", command=reset_manual).pack(side="left", padx=4)
        ttk.Button(buttons, text="Xác nhận và lưu", command=confirm).pack(side="left", padx=4)
        ttk.Button(buttons, text="Tải giọng VOICEVOX", command=load_voices).pack(side="left", padx=4)
        ttk.Button(buttons, text="Đóng", command=dialog.destroy).pack(side="right")
        if auto_analyze and state.speaker_data["status"] == "unreviewed" and not state.speaker_data["assignments"] \
                and self._ready_for_analysis():
            dialog.after(100, analyze)
        elif state.speaker_data["status"] == "stale":
            note.set("Phân vai cũ đã stale. Bấm Phân tích lại bằng AI hoặc sửa thủ công.")
