"""B1 has no live OAuth, API inference, VOICEVOX, or billing calls in tests."""
from copy import deepcopy
import io
import json
import os
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from te.project.models import SectionName
from te.studio.ai_auth import ChatGPTAuth, CredentialStore
from te.studio.ai_clients import AIConnectionError, OpenAIConnection, normalize_reasoning_levels
from te.studio.ai_speakers import (ROLES, filter_assignments, resolve_speaker_references,
                                   resolve_japanese_references,
                                   script_fingerprint, section_counts, segment_passages,
                                   validate_assignments, voice_key, voice_label)
from te.studio.service import StudioService
from te.studio.multi_voice import confirmed_speaker_data, speech_plan


def test_auto_attribution_requires_live_connection_and_loaded_model(monkeypatch):
    from te.studio.ai_gui import SpeakerAIController

    controller = SpeakerAIController.__new__(SpeakerAIController)
    controller.provider = SimpleNamespace(get=lambda: "api")
    controller.model = SimpleNamespace(get=lambda: "gpt-test")
    controller.available = {"api": [{"id": "gpt-test"}], "chatgpt": []}
    controller.credentials = SimpleNamespace(get=lambda _key: None)
    assert not controller._ready_for_analysis()
    controller.credentials = SimpleNamespace(get=lambda _key: "saved-secret")
    assert controller._ready_for_analysis()
    controller.available["api"] = []
    assert not controller._ready_for_analysis()

    controller.provider = SimpleNamespace(get=lambda: "chatgpt")
    controller.available["chatgpt"] = [{"id": "gpt-test"}]
    monkeypatch.setattr(controller, "_auth", lambda: SimpleNamespace(record=lambda: None))
    assert not controller._ready_for_analysis()
    monkeypatch.setattr(controller, "_auth", lambda: SimpleNamespace(record=lambda: {
        "scopes": ["chatgpt.tokens.use.direct", "resource.invoke"],
        "expires_at": 0, "refresh_token": "refresh"}))
    assert controller._ready_for_analysis()


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
def test_japanese_first_review_keeps_ai_request_explicit(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import simpledialog, ttk
    from te.studio.gui import StudioApp

    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "Review GUI")
    root = tk.Tk()
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    app = StudioApp(root, service)
    monkeypatch.setattr(app.ai, "_connection", lambda *_args: (_ for _ in ()).throw(
        AssertionError("AI must not be called when review opens")))
    app.ai.open_review()
    dialog = root.winfo_children()[-1]
    table = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Treeview))
    assert table["columns"] == ("section", "ja", "role", "vi", "review")
    labels = [child.cget("text") for child in dialog.winfo_children() if isinstance(child, ttk.Frame)
              for child in child.winfo_children() if isinstance(child, ttk.Button)]
    assert "Phân vai lại toàn bài bằng AI" in labels
    root.destroy()


def sections():
    return {s.value: {"vi": "", "ja": ""} for s in SectionName} | {
        "P3": {"vi": "A dẫn. C nói: ‘Hay!’ A kết.", "ja": "案内。Cの言葉。結び。"},
        "P4": {"vi": "A mở. D bình luận.", "ja": "導入。Dの意見。"}}


def row(section, role, vi, ja, warning=""):
    return {"section": section, "role": role, "vietnamese": vi, "japanese": ja,
            "confidence": "review" if warning else "high", "warning": warning}


def test_roles_interleaving_and_alignment_mismatch():
    source = sections()
    records = validate_assignments([
        row("P3", "A", [0, 6], [0, 3]),
        row("P3", "C", [7, 20], [3, 8]),
        row("P3", "A", [21, 27], [8, 11]),
        row("P4", "A", [0, 5], [0, 3]),
        row("P4", "D", [6, 18], [3, 8]),
    ], source)
    assert [item["role"] for item in records] == ["A", "C", "A", "A", "D"]
    assert all(item["source"] == "ai" and not item["confirmed"] for item in records)
    assert len(ROLES) == 4


def test_ambiguous_manual_and_invalid_output():
    source = sections()
    unclear = validate_assignments([row("P3", None, [0, 6], None, "Chưa rõ đoạn Nhật")], source)
    assert unclear[0]["confidence"] == "review"
    unclear[0].update(role="A", japanese=[0, 3], source="manual", confirmed=True)
    assert unclear[0]["role"] == "A"
    with pytest.raises(ValueError, match="Vai"):
        validate_assignments([row("P3", "E", [0, 6], [0, 3])], source)
    with pytest.raises(ValueError, match="ngoài script"):
        validate_assignments([row("P3", "A", [0, 999], [0, 3])], source)
    with pytest.raises(ValueError, match="cần xem lại"):
        validate_assignments([row("P3", "A", [0, 6], None)], source)


def test_project_persistence_stale_and_provider_preference_is_separate(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("B1")
    service.set_section(state, SectionName.P3, vietnamese="A dẫn. C nói.", japanese="案内。発言。")
    data = state.speaker_data
    data["identities"]["C"] = "Bình luận viên C"
    data["assignments"] = validate_assignments([row("P3", "C", [7, 13], [3, 6])], state.sections)
    data["assignments"][0].update(source="manual", confirmed=True)
    data["fingerprint"] = script_fingerprint(state.sections)
    data["status"] = "confirmed"
    data["voices"]["C"] = {"speaker_uuid": "speaker-uuid", "style_id": 42,
                            "speaker": "声", "style": "ノーマル"}
    service.save_project(state)
    service.settings["ai"] = {"provider": "api", "models": {"api": "gpt-model"},
                              "reasoning": "Nhanh"}
    service.save_settings()
    state = service.open_project(state.directory)
    assert state.speaker_data["status"] == "confirmed"
    assert state.speaker_data["voices"]["C"]["style_id"] == 42
    service.settings["ai"]["provider"] = "chatgpt"
    service.save_settings()
    assert state.speaker_data["assignments"][0]["role"] == "C"
    service.set_section(state, SectionName.P3, vietnamese="A dẫn. C nói thêm.", japanese="案内。発言。")
    service.save_project(state)
    stale = service.open_project(state.directory).speaker_data
    assert stale["status"] == "stale"
    assert stale["assignments"][0]["role"] == "C" and stale["assignments"][0]["source"] == "manual"
    assert "api_key" not in json.dumps(service.settings)
    assert "api_key" not in (state.directory / "studio.json").read_text(encoding="utf-8")


class FakeCredentials:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_auth_provider_isolation_and_permission_failure(monkeypatch):
    vault = FakeCredentials({"api_key": "sk-test"})
    auth = ChatGPTAuth(vault, "urn:uuid:host")
    assert vault.get("api_key") == "sk-test" and auth.record() is None
    with pytest.raises(PermissionError, match="Chưa kết nối"):
        auth.access_token()
    vault.set("chatgpt", json.dumps({"client_id": "oaiapp_1", "subject": "person",
        "scopes": ["openid"], "access_token": "oauth-test", "expires_at": 9999999999}))
    with pytest.raises(PermissionError, match="chưa cấp quyền"):
        auth.access_token()
    assert vault.get("api_key") == "sk-test"


def test_windows_credential_chunking_and_rotation(monkeypatch):
    class FakeKeyring:
        class errors:
            PasswordDeleteError = KeyError
        values = {}
        def get_password(self, service, name):
            return self.values.get((service, name))
        def set_password(self, service, name, value):
            assert len(value) <= 1100
            self.values[(service, name)] = value
        def delete_password(self, service, name):
            del self.values[(service, name)]
    vault = CredentialStore()
    store = FakeKeyring()
    monkeypatch.setattr(vault, "_keyring", lambda: store)
    vault.set("chatgpt", "a" * 5000)
    assert vault.get("chatgpt") == "a" * 5000
    vault.set("chatgpt", "new")
    assert vault.get("chatgpt") == "new"
    vault.delete("chatgpt")
    assert vault.get("chatgpt") is None and not store.values


def test_chatgpt_dynamic_registration_flow_is_validated_without_live_auth(monkeypatch):
    import te.studio.ai_auth as ai_auth
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit, urlencode
    from urllib.request import urlopen
    vault = FakeCredentials()
    auth = ChatGPTAuth(vault, "urn:uuid:host")
    observed = {}
    def browser(url):
        params = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        observed.update(params)
        assert params["client_id"] == "dynamic_agent_client"
        assert "chatgpt.tokens.use.direct" in params["scope"]
        assert params["ext_agent_host_id"] == "urn:uuid:host"
        callback = params["redirect_uri"] + "?" + urlencode({
            "state": params["state"], "code": "code-test", "client_id": "oaiapp_issued"})
        Thread(target=lambda: urlopen(callback, timeout=3).close(), daemon=True).start()
        return True
    def post(url, fields):
        assert fields["client_id"] == "oaiapp_issued"
        assert fields["grant_type"] == "authorization_code"
        assert fields["redirect_uri"] == observed["redirect_uri"]
        return {"id_token": "id-test", "access_token": "access-test", "token_type": "Bearer",
                "refresh_token": "refresh-test", "expires_in": 3600,
                "scope": "openid resource.invoke chatgpt.tokens.use.direct"}
    monkeypatch.setattr(ai_auth.webbrowser, "open", browser)
    monkeypatch.setattr(ai_auth, "_post_form", post)
    monkeypatch.setattr(auth, "_verify_id_token", lambda token, client_id, nonce:
                        {"sub": "person", "email": "person@example.com"})
    record = auth.connect()
    assert record["client_id"] == "oaiapp_issued"
    assert auth.access_token() == "access-test"
    assert "code-test" not in json.dumps(vault.values)


def test_model_catalogs_stream_contract_and_no_auto_fallback(monkeypatch):
    import te.studio.ai_clients as clients
    calls = []
    source = sections()
    ids = list(segment_passages(source).values())
    response = {"assignments": [{"japanese_id": item["id"],
                                  "role": "C" if item["section"] == "P3" else "A",
                                  "confidence": "high", "warning": ""}
                                 for item in ids if item["language"] == "ja"]}
    stream = (b"data: " + json.dumps({"type": "response.output_text.delta",
              "delta": json.dumps(response)}, ensure_ascii=False).encode() + b"\n\n" +
              b'data: {"type":"response.completed"}\n\n')
    def fake_urlopen(request, timeout):
        calls.append(request)
        if request.full_url.endswith("/models"):
            if request.get_header("Authorization") == "Bearer oauth-test":
                return io.BytesIO(json.dumps({"models": [{"slug": "listed-model", "display_name": "Listed",
                    "visibility": "list", "supported_reasoning_levels": ["low"]}]}).encode())
            return io.BytesIO(json.dumps({"data": [{"id": "gpt-api-model"}]}).encode())
        return io.BytesIO(stream)
    monkeypatch.setattr(clients, "urlopen", fake_urlopen)
    chatgpt = OpenAIConnection("chatgpt", lambda: "oauth-test")
    api = OpenAIConnection("api", lambda: "sk-test")
    assert chatgpt.models()[0]["id"] == "listed-model"
    assert api.models()[0]["id"] == "gpt-api-model"
    for connection, model in ((chatgpt, chatgpt.models()[0]), (api, api.models()[0])):
        result = connection.attribute_speakers(
            {key: value["vi"] for key, value in source.items()},
            {key: value["ja"] for key, value in source.items()}, deepcopy(ROLES),
            {"model": model["id"], "available_models": [model], "reasoning": "Nhanh"})
        assert any(item["role"] == "C" for item in result)
    chat_body = json.loads(calls[-2].data)
    api_body = json.loads(calls[-1].data)
    assert chat_body["store"] is False and chat_body["stream"] is True
    assert "vietnamese_context" in chat_body["input"][0]["content"]
    assert chat_body["reasoning"] == {"effort": "low"}
    assert "reasoning" not in api_body
    assert calls[-2].get_header("Authorization") == "Bearer oauth-test"
    assert calls[-1].get_header("Authorization") == "Bearer sk-test"


def test_dictionary_reasoning_catalog_is_safe_for_gui_and_request(monkeypatch):
    import te.studio.ai_clients as clients
    from te.studio.ai_gui import SpeakerAIController

    requests = []
    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/models"):
            return io.BytesIO(json.dumps({"models": [
                {"slug": "gpt-dict", "visibility": "list", "display_name": "Dictionary model",
                 "supported_reasoning_levels": [
                     {"effort": "low", "label": "Fast"}, {"id": "medium"}, "high",
                     {"unknown": "low"}, None, {"effort": 4}, "low"]},
                {"visibility": "list", "slug": None}, None]}).encode())
        output = {"assignments": [{"japanese_id": item["id"], "role": "A",
                  "confidence": "high", "warning": ""}
                  for item in segment_passages(sections()).values() if item["language"] == "ja"]}
        events = [{"type": "response.output_text.delta", "delta": json.dumps(output)},
                  {"type": "response.completed"}]
        return io.BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events))
    monkeypatch.setattr(clients, "urlopen", fake_urlopen)
    connection = OpenAIConnection("chatgpt", lambda: "mock-token")
    models = connection.models()
    assert models == [{"id": "gpt-dict", "label": "Dictionary model",
                       "reasoning": ["low", "medium", "high"], "structured": False}]

    class Variable:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
    settings = {"ai": {"models": {}}}
    combo = SimpleNamespace(configure=lambda **kwargs: None)
    controller = SpeakerAIController.__new__(SpeakerAIController)
    controller.app = SimpleNamespace(service=SimpleNamespace(settings=settings, save_settings=lambda: None))
    controller.available = {"chatgpt": [], "api": []}
    controller.provider = Variable("chatgpt")
    controller.model = Variable()
    controller.model_combo = combo
    controller.status = Variable()
    controller._models_loaded(models, "chatgpt")
    assert controller.model.get() == "gpt-dict"
    assert controller.status.get() == "Đã tải 1 models. Reasoning hỗ trợ: low, medium, high"

    source = sections()
    connection.attribute_speakers({key: value["vi"] for key, value in source.items()},
                                  {key: value["ja"] for key, value in source.items()}, dict(ROLES),
                                  {"model": controller.model.get(), "available_models": models,
                                   "reasoning": "Nhanh"})
    assert json.loads(requests[-1].data)["reasoning"] == {"effort": "low"}


def test_malformed_reasoning_catalog_metadata_never_crashes_callback():
    from te.studio.ai_gui import SpeakerAIController
    assert normalize_reasoning_levels({"effort": "low"}) == []
    assert normalize_reasoning_levels([{"effort": "surprise"}, 23, None]) == []
    controller = SpeakerAIController.__new__(SpeakerAIController)
    controller.app = SimpleNamespace(service=SimpleNamespace(
        settings={"ai": {"models": {}}}, save_settings=lambda: None))
    controller.available = {"chatgpt": [], "api": []}
    controller.provider = SimpleNamespace(get=lambda: "chatgpt")
    messages = []
    controller.model_combo = SimpleNamespace(configure=lambda **kwargs: None)
    controller.model = SimpleNamespace(set=lambda value: messages.append(value), get=lambda: "bad")
    controller.status = SimpleNamespace(set=lambda value: messages.append(value))
    controller._models_loaded([None, {"id": None}, {"id": "bad", "reasoning": [23, {}]}], "chatgpt")
    assert controller.available["chatgpt"][0]["reasoning"] == []
    assert "Reasoning chưa được catalog xác nhận" in messages[-1]
    controller._models_loaded({"wrong": "shape"}, "chatgpt")
    assert "không có mục hợp lệ" in messages[-1]


def test_api_quota_error_is_reported_without_fallback(monkeypatch):
    import te.studio.ai_clients as clients
    def denied(_request, timeout):
        raise HTTPError("https://api.openai.com/v1/models", 429, "Too Many Requests", {},
                        io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
    monkeypatch.setattr(clients, "urlopen", denied)
    with pytest.raises(AIConnectionError, match="429.*quota"):
        OpenAIConnection("api", lambda: "sk-test").models()


def test_segment_ids_stable_quotes_unicode_and_sentence_switches():
    source = {"P1": {"vi": "A dẫn. C nói: ‘Hay!’ A kết.\n\nTiếp.",
                     "ja": "案内。Cは「いい！」と話す。締め。"}}
    first = list(segment_passages(source).values())
    second = list(segment_passages(deepcopy(source)).values())
    assert first == second
    assert all(source[item["section"]][item["language"]][item["start"]:item["end"]] == item["text"]
               for item in first)
    assert any(item["text"] == "‘Hay!’" for item in first)
    assert any(item["text"] == "「いい！」" for item in first)
    assert len([item for item in first if item["language"] == "vi"]) >= 4


@pytest.mark.parametrize("labels,roles", [
    (("P1", "P2", "P3", "P4"), ("A", "B", "C", "D")),
    (("P1", "P2"), ("A", "B")),
    (("P1",), ("A",)),
])
def test_whole_script_scope_and_role_counts(monkeypatch, labels, roles):
    import te.studio.ai_clients as clients
    source = {label: {"vi": f"{role} kể chuyện.", "ja": f"{role}が話す。"}
              for label, role in zip(labels, roles)}
    passages = list(segment_passages(source).values())
    output = {"assignments": [{"japanese_id": next(item["id"] for item in passages
               if item["section"] == label and item["language"] == "ja"),
               "role": role, "confidence": "high", "warning": ""}
               for label, role in zip(labels, roles)]}
    requests = []
    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        events = [{"type": "response.output_text.delta", "delta": json.dumps(output)},
                  {"type": "response.completed"}]
        return io.BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events))
    monkeypatch.setattr(clients, "urlopen", fake_urlopen)
    connection = OpenAIConnection("chatgpt", lambda: "oauth-test")
    model = {"id": "listed", "reasoning": [], "structured": True}
    result = connection.attribute_speakers({key: value["vi"] for key, value in source.items()},
        {key: value["ja"] for key, value in source.items()}, dict(ROLES),
        {"model": "listed", "available_models": [model], "reasoning": "Nhanh"})
    assert [item["role"] for item in result] == list(roles)
    assert [item["section"] for item in result] == list(labels)
    payload = json.loads(requests[0]["input"][0]["content"])
    assert [item["section"] for item in payload["sections"]] == list(labels)
    assert [item["japanese_script"] for item in payload["sections"]] == [source[label]["ja"] for label in labels]
    assert [item["vietnamese_context"] for item in payload["sections"]] == [source[label]["vi"] for label in labels]
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    props = requests[0]["text"]["format"]["schema"]["properties"]["assignments"]["items"]["properties"]
    assert "japanese_id" in props and "vietnamese_id" not in props
    assert requests[0]["store"] is False and requests[0]["stream"] is True


def test_partial_invalid_refs_keep_valid_rows_and_flag_missing():
    source = {"P3": {"vi": "A dẫn. C nói: ‘Hay!’ A kết.",
                     "ja": "案内。Cは「いい！」と話す。締め。"}}
    passages = list(segment_passages(source).values())
    vi = [item for item in passages if item["language"] == "vi"]
    ja = [item for item in passages if item["language"] == "ja"]
    rows, diagnostics = resolve_speaker_references([
        {"vietnamese_id": vi[0]["id"], "japanese_ids": [ja[0]["id"]],
         "role": "A", "confidence": "high", "warning": ""},
        {"vietnamese_id": vi[1]["id"], "japanese_ids": ["bad-id"],
         "role": "C", "confidence": "high", "warning": ""},
        {"vietnamese_id": vi[2]["id"], "japanese_ids": [ja[2]["id"]],
         "role": "E", "confidence": "high", "warning": ""},
        {"vietnamese_id": "bad-vi", "japanese_ids": None,
         "role": "A", "confidence": "high", "warning": ""},
    ], source)
    assert len(rows) == len(vi)
    assert rows[0]["role"] == "A" and rows[0]["japanese"] == [ja[0]["start"], ja[0]["end"]]
    assert rows[1]["confidence"] == "review" and rows[1]["japanese"] is None
    assert rows[2]["role"] is None and rows[2]["confidence"] == "review"
    assert rows[3]["warning"].startswith("AI bỏ sót")
    assert any("vai không hỗ trợ" in item for item in diagnostics)
    assert any("tham chiếu Nhật" in item for item in diagnostics)
    assert any("ID đoạn Việt" in item for item in diagnostics)


def test_kl4_duplicate_vietnamese_context_resolves_once_per_japanese_id(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("KL4 overlap")
    service.set_section(state, SectionName.P1,
        vietnamese="MC dẫn. Nhân vật trả lời. MC kết.", japanese="司会「発言」締め。")
    service.save_project(state)
    ja = [item for item in segment_passages(state.sections).values()
          if item["language"] == "ja"]
    raw = [{"japanese_id": item["id"], "role": role,
            "confidence": "high", "warning": ""}
           for item, role in zip(ja, ("A", "B", "A"))]
    rows, diagnostics = resolve_japanese_references(raw[::-1], state.sections)
    assert not diagnostics
    assert len(rows) == len(ja) == 3
    assert [row["japanese"] for row in rows] == [[item["start"], item["end"]] for item in ja]
    assert all(row["vietnamese"] is None for row in rows)
    assert state.speaker_data["assignments"] == []  # AI draft has not replaced saved data.
    candidate = confirmed_speaker_data(state, rows, state.speaker_data["identities"])
    state.speaker_data = candidate
    service.save_project(state)
    plan, warnings = speech_plan(state)
    assert not warnings
    assert "".join(text for parts in plan.values() for _, text in parts) == state.sections["P1"]["ja"]

    with pytest.raises(ValueError, match="lặp"):
        resolve_japanese_references([raw[0], raw[0], raw[2]], state.sections)
    with pytest.raises(ValueError, match="chưa phân vai đủ"):
        resolve_japanese_references(raw[:-1], state.sections)


def test_mocked_gpt_duplicate_japanese_id_is_rejected(monkeypatch):
    import te.studio.ai_clients as clients
    source = {"P1": {"vi": "Hai đoạn Việt cùng nói một ý.", "ja": "前半。後半。"}}
    ja = [item for item in segment_passages(source).values() if item["language"] == "ja"]
    duplicate = {"assignments": [
        {"japanese_id": ja[0]["id"], "role": "A", "confidence": "high", "warning": ""},
        {"japanese_id": ja[0]["id"], "role": "B", "confidence": "high", "warning": ""}]}
    events = [{"type": "response.output_text.delta", "delta": json.dumps(duplicate)},
              {"type": "response.completed"}]
    monkeypatch.setattr(clients, "urlopen", lambda _request, timeout: io.BytesIO(
        b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)))
    connection = OpenAIConnection("api", lambda: "mock-token")
    with pytest.raises(AIConnectionError, match="assignments"):
        connection.attribute_speakers({"P1": source["P1"]["vi"]}, {"P1": source["P1"]["ja"]},
            dict(ROLES), {"model": "mock", "available_models": [{"id": "mock", "reasoning": [],
                                                                  "structured": True}], "reasoning": "Nhanh"})


def test_confirmation_rejects_overlap_gaps_and_unresolved_role(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("Review gate")
    service.set_section(state, SectionName.P1, vietnamese="Một. Hai.", japanese="案内。発言。")
    service.save_project(state)
    original = deepcopy(state.speaker_data)
    passages = [item for item in segment_passages(state.sections).values() if item["language"] == "ja"]
    rows, _ = resolve_japanese_references([
        {"japanese_id": item["id"], "role": "A" if index == 0 else "B",
         "confidence": "high", "warning": ""}
        for index, item in enumerate(passages)], state.sections)
    with pytest.raises(ValueError, match="chồng lấn"):
        confirmed_speaker_data(state, [rows[0], dict(rows[1], japanese=rows[0]["japanese"])],
                               original["identities"])
    with pytest.raises(ValueError, match="chưa phân vai"):
        confirmed_speaker_data(state, rows[:1], original["identities"])
    with pytest.raises(ValueError, match="chưa xác nhận"):
        confirmed_speaker_data(state, [rows[0], dict(rows[1], role=None, confidence="review")],
                               original["identities"])
    assert state.speaker_data == original


def test_filter_story_order_and_counts_without_network():
    rows = [row("P4", "D", [0, 2], [0, 2]), row("P1", "A", [4, 6], [4, 6]),
            row("P1", "B", [0, 3], [0, 3])]
    assert [item["role"] for item in filter_assignments(rows, "Toàn bài")] == ["B", "A", "D"]
    assert [item["role"] for item in filter_assignments(rows, "P1")] == ["B", "A"]
    assert filter_assignments(rows, "P2") == []
    assert section_counts(rows) == {"P1": 2, "P2": 0, "P3": 0, "P4": 1}


def test_resolved_assignment_manual_correction_survives_reopen(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("Reference IDs")
    service.set_section(state, SectionName.P1, vietnamese="MC dẫn. B nói.", japanese="司会。Bが話す。")
    passages = list(segment_passages(state.sections).values())
    vi = next(item for item in passages if item["section"] == "P1" and item["language"] == "vi")
    ja = next(item for item in passages if item["section"] == "P1" and item["language"] == "ja")
    rows, diagnostics = resolve_speaker_references([{"vietnamese_id": vi["id"],
        "japanese_ids": [ja["id"]], "role": "B", "confidence": "high", "warning": ""}], state.sections)
    assert diagnostics
    rows[0].update(role="A", source="manual", confirmed=True)
    state.speaker_data.update(assignments=rows, fingerprint=script_fingerprint(state.sections),
                              status="confirmed")
    service.save_project(state)
    reopened = service.open_project(state.directory)
    assert reopened.speaker_data["assignments"][0]["role"] == "A"
    assert reopened.speaker_data["assignments"][0]["source"] == "manual"
    assert reopened.speaker_data["assignments"][0]["japanese"] == [ja["start"], ja["end"]]


def test_four_role_voices_save_reload_switch_and_edit_independently(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("KL4")
    service.set_section(state, SectionName.P1, vietnamese="MC kể.", japanese="司会。")
    service.save_project(state)
    voices = {role: {"speaker_uuid": f"speaker-{role}", "style_id": index,
                     "speaker": f"Giọng {role}", "style": "Normal"}
              for index, role in enumerate(ROLES, 1)}
    for role in ("C", "A", "D", "B"):
        service.save_role_configuration(state, role, f"Nhân vật {role}", voices[role])
        reopened = service.open_project(state.directory)
        assert reopened.speaker_data["voices"][role] == voices[role]
        assert reopened.speaker_data["identities"][role] == f"Nhân vật {role}"
        assert reopened.speaker_data["voices"] == state.speaker_data["voices"]
    before_assignments = deepcopy(state.speaker_data["assignments"])
    before_voices = deepcopy(state.speaker_data["voices"])
    service.save_role_configuration(state, "B", "Tên B mới", voices["A"])
    reopened = service.open_project(state.directory)
    assert reopened.speaker_data["voices"]["B"] == voices["A"]  # same style may be reused
    assert all(reopened.speaker_data["voices"][role] == before_voices[role]
               for role in ("A", "C", "D"))
    assert reopened.speaker_data["assignments"] == before_assignments
    assert reopened.speaker_data["identities"]["B"] == "Tên B mới"
    assert reopened.speaker_data["voices"]["A"]["style_id"] == 1
    service.save_role_configuration(reopened, "C", "Tên C mới")  # name only keeps the voice
    assert service.open_project(state.directory).speaker_data["voices"]["C"] == voices["C"]
    assert voice_key(voices["A"]) == ("speaker-A", 1)
    assert voice_label(voices["A"]) == "Giọng A — Normal (ID 1)"


def test_unused_role_needs_no_voice_and_legacy_voice_label_survives(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    state = service.create_project("Old project")
    service.save_role_configuration(state, "A", "MC")
    legacy = {"speaker_uuid": "old-uuid", "style_id": 91}
    service.save_role_configuration(state, "D", "D", legacy)
    reopened = service.open_project(state.directory)
    assert set(reopened.speaker_data["voices"]) == {"D"}
    assert voice_label(reopened.speaker_data["voices"]["D"]) == "old-uuid — style #91 (ID 91)"
    assert ROLES == {"A": "MC", "B": "Nhân vật chính", "C": "Bình luận C", "D": "Bình luận D"}


@pytest.mark.parametrize("body,expected", [
    (b"not-json", "JSON không hợp lệ"),
    (b"{}", "thiếu trường assignments"),
    (b'{"assignments":{}}', "cấu trúc assignments"),
])
def test_specific_response_diagnostics(monkeypatch, body, expected):
    import te.studio.ai_clients as clients
    def fake_urlopen(request, timeout):
        return io.BytesIO(b"data: " + json.dumps({"type": "response.output_text.delta",
            "delta": body.decode()}).encode() + b"\n\n" +
            b'data: {"type":"response.completed"}\n\n')
    monkeypatch.setattr(clients, "urlopen", fake_urlopen)
    connection = OpenAIConnection("api", lambda: "sk-test")
    with pytest.raises(AIConnectionError, match=expected):
        connection.attribute_speakers({"P1": "Một."}, {"P1": "一。"}, dict(ROLES),
            {"model": "gpt-test", "available_models": [{"id": "gpt-test", "reasoning": []}]})
