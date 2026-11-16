"""Official API client behavior with no credentials, requests, or uploads in CI."""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from te.studio.publisher import PublisherStore
from te.studio.youtube_api import (CredentialVault, OwnerOAuth, SCOPES, YouTubeAPIClient,
                                   YouTubeAPIError, require_scopes, safe_api_error,
                                   validate_client_config)

A = "UC" + "a" * 22
B = "UC" + "b" * 22
VID = "abcdefghijk"


class Credentials:
    scopes = SCOPES
    granted_scopes = SCOPES
    refresh_token = "fake-refresh"
    valid = True
    def to_json(self):
        return json.dumps({"refresh_token": self.refresh_token})


class Execute:
    def __init__(self, result):
        self.result = result
    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Videos:
    def __init__(self, calls, video_response):
        self.calls = calls
        self.video_response = video_response
    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return SimpleNamespace(next_chunk=lambda: (None, self.video_response))
    def list(self, **kwargs):
        self.calls.append(("video-list", kwargs))
        return Execute({"items": [{"snippet": {"channelId": A}, "status": {"privacyStatus": "private"}}]})


class Service:
    def __init__(self, channel, calls, video_response=None):
        self.channel = channel
        self.calls = calls
        self.video_response = video_response or {"id": VID}
    def channels(self):
        return SimpleNamespace(list=lambda **kwargs: self._channels_list(kwargs))
    def _channels_list(self, kwargs):
        self.calls.append(("channels", kwargs))
        return Execute(self.channel)
    def videos(self):
        return Videos(self.calls, self.video_response)
    def thumbnails(self):
        def set_thumbnail(**kwargs):
            self.calls.append(("thumbnail", kwargs))
            return Execute({"items": [{}]})
        return SimpleNamespace(set=set_thumbnail)


def channel(channel_id=A, name="同じ名前"):
    return {"items": [{"id": channel_id, "snippet": {"title": name,
            "thumbnails": {"default": {"url": "https://example.test/image.png"}}}}]}


def api_for(result, calls, response=None):
    service = Service(result, calls, response)
    return YouTubeAPIClient(service_factory=lambda credentials: service,
                            media_factory=lambda path, **kwargs: (path, kwargs))


def client_config():
    return {"installed": {"client_id": "fake.apps.googleusercontent.com", "client_secret": "fake",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}


def test_client_config_and_scope_restrictions():
    assert validate_client_config(client_config())
    with pytest.raises(ValueError, match="chính thức"):
        validate_client_config({"installed": {**client_config()["installed"], "token_uri": "https://evil.test"}})
    with pytest.raises(ValueError, match="Desktop"):
        validate_client_config({"web": client_config()["installed"]})
    with pytest.raises(YouTubeAPIError, match="youtube.upload"):
        require_scopes(SimpleNamespace(granted_scopes=[SCOPES[1]]))
    require_scopes(SimpleNamespace(granted_scopes=" ".join(SCOPES)), initial_grant=True)
    with pytest.raises(YouTubeAPIError, match="thực tế"):
        require_scopes(SimpleNamespace(granted_scopes=None, scopes=SCOPES), initial_grant=True)
    assert not any("gmail" in scope or "analytics" in scope or "youtubepartner" in scope for scope in SCOPES)


def test_exact_channel_verification_multiple_names_and_ambiguity():
    calls = []
    creds = Credentials()
    assert api_for(channel(A), calls).verify_channel(creds)["id"] == A
    assert calls[0] == ("channels", {"part": "snippet", "mine": True, "maxResults": 50})
    with pytest.raises(YouTubeAPIError, match="kênh khác"):
        api_for(channel(B), []).verify_exact_channel(creds, A)
    for result in ({"items": []}, {"items": channel(A)["items"] + channel(B)["items"]}):
        with pytest.raises(YouTubeAPIError, match="đúng một kênh"):
            api_for(result, []).verify_channel(creds)


def test_private_resumable_upload_metadata_thumbnail_and_reconciliation(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test video")
    image = tmp_path / "image.png"
    image.write_bytes(b"test image")
    calls = []
    api = api_for(channel(A), calls)
    created = []
    result = api.upload_private(Credentials(), A, video,
        {"title": "日本語の題名", "description": "日本語\n二行目", "tags": "野球, 日本", "audience": "not_made_for_kids"},
        on_created=created.append, thumbnail=image)
    assert created == [VID]
    assert result["video_id"] == VID and result["thumbnail_error"] == ""
    insert = next(value for kind, value in calls if kind == "insert")
    assert insert["body"] == {"snippet": {"title": "日本語の題名", "description": "日本語\n二行目",
        "defaultLanguage": "ja", "tags": ["野球", "日本"]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    assert insert["media_body"][1]["resumable"] is True
    assert next(value for kind, value in calls if kind == "thumbnail")["videoId"] == VID
    assert api.reconcile_known_video(Credentials(), A, VID)["privacy"] == "private"
    with pytest.raises(YouTubeAPIError, match="kênh khác"):
        api_for(channel(B), []).upload_private(Credentials(), A, video,
            {"title": "title", "audience": "made_for_kids"}, on_created=lambda _: None)


def test_failed_resumable_response_and_missing_audience_do_not_claim_success(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test video")
    with pytest.raises(ValueError, match="đối tượng"):
        YouTubeAPIClient.private_body({"title": "title"})
    with pytest.raises(ValueError, match="hẹn giờ"):
        YouTubeAPIClient.private_body({"title": "title", "audience": "made_for_kids", "mode": "scheduled"})
    created = []
    api = api_for(channel(A), [], {"unexpected": "no id"})
    with pytest.raises(YouTubeAPIError, match="chưa xác nhận video ID"):
        api.upload_private(Credentials(), A, video,
            {"title": "title", "audience": "made_for_kids"}, on_created=created.append)
    assert created == []
    err = SimpleNamespace(resp=SimpleNamespace(status=403),
                          content=b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}',
                          __str__=lambda: "secret")
    assert "quotaExceeded" in str(safe_api_error(err))
    assert "secret" not in str(safe_api_error(err))


def test_schedule_plan_uses_tokyo_timezone_but_private_upload_stays_gated():
    metadata = {"mode": "scheduled", "timezone": "Asia/Tokyo", "scheduled_local": "2030-05-06 20:30",
                "title": "予定", "audience": "not_made_for_kids"}
    planned = YouTubeAPIClient.planned_schedule_status(metadata)
    assert planned == {"privacyStatus": "private", "publishAt": "2030-05-06T20:30:00+09:00"}
    with pytest.raises(ValueError, match="hẹn giờ"):
        YouTubeAPIClient.private_body(metadata)


def test_store_authorizations_refs_and_legacy_migration(tmp_path):
    store = PublisherStore(tmp_path / "settings.json")
    store.add_verified_channel(A, "Cũ")
    assert store.authorized_channels() == []
    with pytest.raises(ValueError, match="OAuth"):
        store.prepare("p", {"channel_id": A, "title": "題名"}, {"sha256": "x"})
    for channel_id, ref in ((A, "a" * 32), (B, "b" * 32)):
        store.register_authorized_channel({"id": channel_id, "name": "同じ名前", "credential_ref": ref,
            "scopes": list(SCOPES), "authorization_status": "authorized", "last_checked_at": "now"})
    with pytest.raises(ValueError, match="đã được OAuth"):
        store.register_authorized_channel({"id": A, "name": "同じ名前", "credential_ref": "c" * 32,
            "scopes": list(SCOPES), "authorization_status": "authorized"})
    with pytest.raises(ValueError, match="OAuth authorization"):
        store.register_authorized_channel({"id": A, "name": "Tên", "credential_ref": "c" * 32,
            "scopes": [SCOPES[0]], "authorization_status": "authorized"})
    assert len(store.authorized_channels()) == 2
    job = store.prepare("p", {"channel_id": A, "title": "題名"}, {"sha256": "x", "path": "x.mp4"})
    assert job["channel_id"] == A and job["credential_ref"] == "a" * 32
    saved = (store.path).read_text(encoding="utf-8")
    assert "fake-refresh" not in saved and "同じ名前" in saved
    assert len(PublisherStore(tmp_path / "settings.json").authorized_channels()) == 2
    store.revoke_channel(A)
    with pytest.raises(ValueError, match="thu hồi"):
        store.begin_attempt(job["id"])
    assert len(store.authorized_channels()) == 1
    assert store.latest("p")["id"] == job["id"]


def test_owner_oauth_system_browser_state_failure_and_no_token_storage(tmp_path, monkeypatch):
    config = tmp_path / "client.json"
    config.write_text(json.dumps(client_config()), encoding="utf-8")
    class Vault:
        saved = []
        def save(self, ref, credentials):
            self.saved.append((ref, credentials))
    vault = Vault()
    flow_module = ModuleType("google_auth_oauthlib.flow")
    package = ModuleType("google_auth_oauthlib")
    def create(config, scopes):
        assert tuple(scopes) == SCOPES
        assert config["installed"]["client_id"] == client_config()["installed"]["client_id"]
        return SimpleNamespace(run_local_server=lambda **kwargs: (_ for _ in ()).throw(ValueError("OAuth state mismatch")))
    flow_module.InstalledAppFlow = SimpleNamespace(from_client_config=create)
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)
    owner = OwnerOAuth(config, vault, api_for(channel(A), []))
    with pytest.raises(YouTubeAPIError, match="không hoàn tất"):
        owner.authorize()
    assert vault.saved == []
    options = []
    flow_module.InstalledAppFlow = SimpleNamespace(from_client_config=lambda config, scopes:
        SimpleNamespace(run_local_server=lambda **kwargs: options.append(kwargs) or Credentials()))
    record = owner.authorize()
    assert record["id"] == A and record["credential_ref"] and len(vault.saved) == 1
    assert record["connection_id"] and record["connection_id"] != record["credential_ref"]
    assert record["credential_ref"] != Credentials().refresh_token
    assert options[0]["host"] == "127.0.0.1" and options[0]["open_browser"] is True
    assert options[0]["authorization_prompt_message"] is None
    assert options[0]["access_type"] == "offline" and options[0]["prompt"] == "consent"


def test_vault_uses_keyring_reference_and_revoke_failure_keeps_token(tmp_path, monkeypatch):
    values = {}
    keyring = SimpleNamespace(set_password=lambda service, ref, value: values.__setitem__((service, ref), value),
                              get_password=lambda service, ref: values.get((service, ref)),
                              delete_password=lambda service, ref: values.pop((service, ref)))
    vault = CredentialVault(keyring)
    vault.save("a" * 32, Credentials())
    assert ("TEStudio.YouTube.Owner", "a" * 32) in values
    owner = OwnerOAuth(tmp_path / "unused", vault, api_for(channel(A), []))
    monkeypatch.setattr(vault, "load", lambda ref: Credentials())
    def failed(request, timeout):
        raise OSError("offline")
    monkeypatch.setattr("te.studio.youtube_api.urlopen", failed)
    with pytest.raises(YouTubeAPIError, match="Không thu hồi"):
        owner.revoke("a" * 32)
    assert ("TEStudio.YouTube.Owner", "a" * 32) in values


def test_token_refresh_and_revocation_are_local_owner_operations(tmp_path, monkeypatch):
    creds = Credentials()
    creds.valid = False
    refreshed = []
    creds.refresh = lambda request: (refreshed.append(True), setattr(creds, "valid", True))
    class Vault:
        deleted = []
        saved = []
        def load(self, reference):
            assert reference == "a" * 32
            return creds
        def save(self, reference, value):
            self.saved.append(reference)
        def delete(self, reference):
            self.deleted.append(reference)
    vault = Vault()
    google = ModuleType("google")
    auth = ModuleType("google.auth")
    transport = ModuleType("google.auth.transport")
    requests = ModuleType("google.auth.transport.requests")
    requests.Request = object
    for name, module in (("google", google), ("google.auth", auth),
                         ("google.auth.transport", transport), ("google.auth.transport.requests", requests)):
        monkeypatch.setitem(sys.modules, name, module)
    owner = OwnerOAuth(tmp_path / "unused", vault, api_for(channel(A), []))
    assert owner.credentials_for("a" * 32) is creds
    assert refreshed == [True] and vault.saved == ["a" * 32]
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    sent = []
    monkeypatch.setattr("te.studio.youtube_api.urlopen", lambda request, timeout: sent.append(request) or Response())
    owner.revoke("a" * 32)
    assert vault.deleted == ["a" * 32]
    assert sent[0].full_url == "https://oauth2.googleapis.com/revoke"
    assert sent[0].get_method() == "POST"


def test_thumbnail_failure_keeps_confirmed_video_id(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"mp4")
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    calls = []
    service = Service(channel(A), calls)
    service.thumbnails = lambda: SimpleNamespace(set=lambda **kwargs: Execute(OSError("network")))
    api = YouTubeAPIClient(service_factory=lambda credentials: service,
                           media_factory=lambda path, **kwargs: (path, kwargs))
    created = []
    result = api.upload_private(Credentials(), A, video,
        {"title": "題名", "audience": "not_made_for_kids"}, created.append, image)
    assert created == [VID]
    assert result["video_id"] == VID and result["thumbnail_error"]
    assert "network" not in result["thumbnail_error"]
