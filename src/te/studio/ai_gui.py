"""Tkinter AI settings and Vietnamese-first speaker review; no render integration."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from te.project.models import SectionName
from te.studio.ai_auth import ChatGPTAuth, CredentialStore
from te.studio.ai_clients import OpenAIConnection
from te.studio.ai_speakers import (ROLES, passage, script_fingerprint,
                                   validate_speaker_data)
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
        box = self.app._group("AI Settings và phân vai A/B/C/D")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Kết nối").pack(side="left")
        ttk.Combobox(row, textvariable=self.provider, values=("chatgpt", "api"),
                     state="readonly", width=14).pack(side="left", padx=5)
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
        controls = ttk.Frame(box)
        controls.pack(fill="x", pady=3)
        ttk.Label(controls, text="Model").pack(side="left")
        self.model_combo = ttk.Combobox(controls, textvariable=self.model, state="readonly", width=35)
        self.model_combo.pack(side="left", padx=5)
        self.model_combo.bind("<<ComboboxSelected>>", lambda _event: self._save_model())
        ttk.Button(controls, text="Tải models", command=self._load_models).pack(side="left")
        ttk.Label(controls, text="Reasoning").pack(side="left", padx=(12, 2))
        ttk.Combobox(controls, textvariable=self.reasoning, values=("Nhanh", "Vừa"),
                     state="readonly", width=10).pack(side="left")
        self.reasoning.trace_add("write", lambda *_: self._save_reasoning())
        ttk.Button(controls, text="AI phân vai", command=self.open_review).pack(side="left", padx=10)
        ttk.Label(box, textvariable=self.status).pack(anchor="w")
        ttk.Label(box, text="Giọng A/B/C/D chỉ lưu cho B2; render hiện tại vẫn dùng một giọng VOICEVOX.").pack(anchor="w")
        self._provider_changed()

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
            status = self.app.state.speaker_data["status"]
            if status == "stale":
                self.status.set("Phân vai cũ đã stale do script đổi; hãy xem lại trước khi xác nhận.")

    def open_review(self):
        app = self.app
        if not app.state or not app._save():
            return
        state = app.state
        dialog = tk.Toplevel(app.root)
        dialog.title("AI phân vai — xem tiếng Việt trước")
        dialog.geometry("1250x850")
        dialog.transient(app.root)
        dialog.grab_set()
        top = ttk.Frame(dialog, padding=8)
        top.pack(fill="x")
        identities = {role: tk.StringVar(value=state.speaker_data["identities"].get(role, ROLES[role]))
                      for role in ROLES}
        for role in ROLES:
            ttk.Label(top, text=f"{role} — {ROLES[role]}").pack(side="left")
            ttk.Entry(top, textvariable=identities[role], width=16).pack(side="left", padx=4)
        section = tk.StringVar(value="P1")
        row = ttk.Frame(dialog, padding=8)
        row.pack(fill="x")
        ttk.Label(row, text="Phần").pack(side="left")
        ttk.Combobox(row, textvariable=section, values=[s.value for s in SectionName],
                     state="readonly", width=6).pack(side="left", padx=5)
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
        role = tk.StringVar(value="A")
        ttk.Combobox(row, textvariable=role, values=("A", "B", "C", "D", ""),
                     state="readonly", width=5).pack(side="left")
        note = tk.StringVar(value="Chọn dòng để sửa; chọn text trong hai ô để gắn span.")
        ttk.Label(dialog, textvariable=note).pack(anchor="w", padx=10)

        def refresh():
            table.delete(*table.get_children())
            for index, item in enumerate(state.speaker_data["assignments"]):
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
        def select_row(_event):
            selected = table.selection()
            if selected:
                item = state.speaker_data["assignments"][int(selected[0])]
                section.set(item["section"])
                role.set(item["role"] or "")
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
            item.update(section=section.get(), role=role.get() or None, vietnamese=vi, japanese=ja,
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

        def analyze():
            if not self.model.get() or not self.available[self.provider.get()]:
                note.set("Hãy kết nối và tải models trong AI Settings trước.")
                return
            if state.speaker_data["status"] != "stale" and state.speaker_data["assignments"]:
                if not messagebox.askyesno("Phân tích lại", "Gọi AI lại và thay các gợi ý AI cũ? "
                                           "Các sửa thủ công được giữ.", parent=dialog):
                    return
            source = deepcopy(state.sections)
            names = {key: value.get() for key, value in identities.items()}
            provider = self.provider.get()
            settings = {"model": self.model.get(), "reasoning": self.reasoning.get(),
                        "available_models": deepcopy(self.available[provider])}
            note.set("AI đang phân vai; bạn vẫn có thể xem script…")
            def work():
                connection = self._connection(provider)
                items = connection.attribute_speakers(
                    {s.value: source[s.value]["vi"] for s in SectionName},
                    {s.value: source[s.value]["ja"] for s in SectionName}, names, settings)
                return items, connection.last_usage
            def done(result):
                if not dialog.winfo_exists():
                    return
                items, usage = result
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
                refresh()
                tokens = (f" Tokens: {usage.get('total_tokens')}." if usage and
                          usage.get("total_tokens") is not None else "")
                note.set(f"Đã nhận {len(items)} gợi ý.{tokens} Kiểm tra và xác nhận thủ công.")
            app._background(work, done)

        def load_voices():
            def done(speakers):
                if not dialog.winfo_exists():
                    return
                choices = {f"{speaker['name']} — {style['name']}":
                           {"speaker_uuid": speaker.get("speaker_uuid", ""), "style_id": style["id"],
                            "speaker": speaker["name"], "style": style["name"]}
                           for speaker in speakers for style in speaker.get("styles", [])}
                voice_combo.configure(values=list(choices))
                voice_combo._choices = choices
                note.set(f"Đã tìm thấy {len(choices)} VOICEVOX styles.")
            app._background(lambda: VoicevoxProvider(timeout=3).list_voices(state.project.tts_settings), done)

        def assign_voice():
            choice = getattr(voice_combo, "_choices", {}).get(voice_choice.get())
            if not choice:
                note.set("Hãy tải và chọn style VOICEVOX.")
                return
            state.speaker_data["voices"][role.get()] = choice
            app.service.save_project(state)
            note.set(f"Đã lưu style cho vai {role.get()} (B2 sẽ dùng khi render đa giọng).")

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
            state.speaker_data["identities"] = {key: value.get() for key, value in identities.items()}
            for item in rows:
                item["confirmed"] = True
            state.speaker_data["fingerprint"] = script_fingerprint(state.sections)
            state.speaker_data["status"] = "confirmed"
            try:
                state.speaker_data = validate_speaker_data(state.speaker_data, state.sections)
                app.service.save_project(state)
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
            refresh()
            note.set("Đã tạo bản nháp trống. Chọn đoạn Việt/Nhật để gán vai thủ công.")

        buttons = ttk.Frame(dialog, padding=8)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="AI phân vai", command=analyze).pack(side="left", padx=4)
        ttk.Button(buttons, text="Thêm / sửa thủ công", command=edit_row).pack(side="left", padx=4)
        ttk.Button(buttons, text="Tạo mới thủ công", command=reset_manual).pack(side="left", padx=4)
        ttk.Button(buttons, text="Xác nhận và lưu", command=confirm).pack(side="left", padx=4)
        ttk.Button(buttons, text="Tải giọng VOICEVOX", command=load_voices).pack(side="left", padx=4)
        voice_choice = tk.StringVar()
        voice_combo = ttk.Combobox(buttons, textvariable=voice_choice, state="readonly", width=28)
        voice_combo.pack(side="left", padx=4)
        ttk.Button(buttons, text="Lưu giọng cho vai", command=assign_voice).pack(side="left", padx=4)
        ttk.Button(buttons, text="Đóng", command=dialog.destroy).pack(side="right")
