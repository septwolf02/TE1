"""B1 has no live OAuth, API inference, VOICEVOX, or billing calls in tests."""
from copy import deepcopy
import io
import json
from urllib.error import HTTPError

import pytest

from te.project.models import SectionName
from te.studio.ai_auth import ChatGPTAuth, CredentialStore
from te.studio.ai_clients import AIConnectionError, OpenAIConnection
from te.studio.ai_speakers import (ROLES, script_fingerprint, validate_assignments)
from te.studio.service import StudioService


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
    response = {"assignments": [row("P3", "C", [7, 20], [3, 8])]}
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
    source = sections()
    for connection, model in ((chatgpt, chatgpt.models()[0]), (api, api.models()[0])):
        result = connection.attribute_speakers(
            {key: value["vi"] for key, value in source.items()},
            {key: value["ja"] for key, value in source.items()}, deepcopy(ROLES),
            {"model": model["id"], "available_models": [model], "reasoning": "Nhanh"})
        assert result[0]["role"] == "C"
    chat_body = json.loads(calls[-2].data)
    api_body = json.loads(calls[-1].data)
    assert chat_body["store"] is False and chat_body["stream"] is True
    assert chat_body["reasoning"] == {"effort": "low"}
    assert "reasoning" not in api_body
    assert calls[-2].get_header("Authorization") == "Bearer oauth-test"
    assert calls[-1].get_header("Authorization") == "Bearer sk-test"


def test_api_quota_error_is_reported_without_fallback(monkeypatch):
    import te.studio.ai_clients as clients
    def denied(_request, timeout):
        raise HTTPError("https://api.openai.com/v1/models", 429, "Too Many Requests", {},
                        io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
    monkeypatch.setattr(clients, "urlopen", denied)
    with pytest.raises(AIConnectionError, match="429.*quota"):
        OpenAIConnection("api", lambda: "sk-test").models()
