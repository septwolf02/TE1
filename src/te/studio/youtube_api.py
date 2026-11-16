"""Owner-only OAuth prototype and official YouTube Data API upload client.

Credentials stay in the operating system keyring. This module is not an
employee publishing service and never confers Editor access through the API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from uuid import uuid4

from .publisher import CHANNEL_ID, VIDEO_ID, normalize_metadata, validate_schedule

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
SCOPES = (UPLOAD_SCOPE, READ_SCOPE)
KEYRING_SERVICE = "TEStudio.YouTube.Owner"


class YouTubeAPIError(RuntimeError):
    pass


def validate_client_config(raw: object) -> dict:
    app = raw.get("installed") if isinstance(raw, dict) else None
    if not isinstance(app, dict) or any(not isinstance(app.get(k), str) or not app[k]
                                      for k in ("client_id", "client_secret", "auth_uri", "token_uri")):
        raise ValueError("Cần JSON OAuth client loại Desktop app từ Google Cloud Console.")
    if app["auth_uri"] not in {"https://accounts.google.com/o/oauth2/auth",
                               "https://accounts.google.com/o/oauth2/v2/auth"} or \
            app["token_uri"] != "https://oauth2.googleapis.com/token":
        raise ValueError("Địa chỉ OAuth trong cấu hình không phải endpoint chính thức của Google.")
    return raw


def safe_api_error(exc: Exception) -> YouTubeAPIError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason = ""
    try:
        payload = json.loads(getattr(exc, "content", b"{}").decode("utf-8"))
        reason = payload["error"]["errors"][0]["reason"]
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        pass
    allowed = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded",
               "insufficientPermissions", "authError", "forbidden", "channelNotFound",
               "invalidCredentials", "youtubeSignupRequired"}
    label = reason if reason in allowed else "API request failed"
    return YouTubeAPIError(f"YouTube API lỗi HTTP {status if isinstance(status, int) else '?'}: {label}.")


class CredentialVault:
    def __init__(self, keyring_module=None):
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise YouTubeAPIError("Cần cài keyring trên máy của chủ kênh.") from exc
        self.keyring = keyring_module

    def save(self, reference: str, credentials) -> None:
        try:
            self.keyring.set_password(KEYRING_SERVICE, reference, credentials.to_json())
        except Exception as exc:
            raise YouTubeAPIError("Không lưu được credential vào kho bảo mật của hệ điều hành.") from exc

    def load(self, reference: str):
        try:
            value = self.keyring.get_password(KEYRING_SERVICE, reference)
        except Exception as exc:
            raise YouTubeAPIError("Không đọc được credential từ kho bảo mật của hệ điều hành.") from exc
        if not value:
            raise YouTubeAPIError("Không tìm thấy credential của kênh trên máy này. Kết nối lại bằng tài khoản chủ kênh.")
        try:
            from google.oauth2.credentials import Credentials
            return Credentials.from_authorized_user_info(json.loads(value), scopes=SCOPES)
        except Exception as exc:
            raise YouTubeAPIError("Credential của kênh không hợp lệ; hãy kết nối lại.") from exc

    def delete(self, reference: str) -> None:
        try:
            self.keyring.delete_password(KEYRING_SERVICE, reference)
        except Exception as exc:
            raise YouTubeAPIError("Không xóa được credential khỏi kho bảo mật của hệ điều hành.") from exc


def require_scopes(credentials, *, initial_grant: bool = False) -> None:
    actual = getattr(credentials, "granted_scopes", None)
    if initial_grant and actual is None:
        raise YouTubeAPIError("Google chưa xác nhận các scope thực tế đã cấp; không thể đăng ký kênh.")
    values = actual if actual is not None else (getattr(credentials, "scopes", None) or ())
    granted = set(values.split() if isinstance(values, str) else values)
    if not set(SCOPES).issubset(granted):
        raise YouTubeAPIError("OAuth chưa cấp đủ youtube.upload và youtube.readonly; không thể đăng ký kênh.")


class YouTubeAPIClient:
    def __init__(self, service_factory=None, media_factory=None):
        if service_factory is None:
            from googleapiclient.discovery import build
            service_factory = lambda credentials: build("youtube", "v3", credentials=credentials,
                                                        cache_discovery=False)
        if media_factory is None:
            from googleapiclient.http import MediaFileUpload
            media_factory = MediaFileUpload
        self.service_factory = service_factory
        self.media_factory = media_factory

    def verify_channel(self, credentials) -> dict:
        require_scopes(credentials)
        try:
            result = self.service_factory(credentials).channels().list(
                part="snippet", mine=True, maxResults=50).execute()
        except Exception as exc:
            raise safe_api_error(exc) from exc
        items = result.get("items", []) if isinstance(result, dict) else []
        if len(items) != 1:
            raise YouTubeAPIError("OAuth không xác định được đúng một kênh. Chọn một kênh trong Google rồi ủy quyền lại.")
        item = items[0]
        channel_id = item.get("id")
        snippet = item.get("snippet") or {}
        title = snippet.get("title")
        if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id) or \
                not isinstance(title, str) or not title.strip():
            raise YouTubeAPIError("YouTube API không trả về ID và tên kênh hợp lệ.")
        thumbnails = snippet.get("thumbnails") or {}
        thumb = next((entry.get("url", "") for entry in (thumbnails.get("default"), thumbnails.get("medium"))
                      if isinstance(entry, dict) and isinstance(entry.get("url"), str)), "")
        return {"id": channel_id, "name": title, "thumbnail_url": thumb}

    def verify_exact_channel(self, credentials, expected_id: str) -> dict:
        actual = self.verify_channel(credentials)
        if actual["id"] != expected_id:
            raise YouTubeAPIError("Credential đang trỏ tới kênh khác; upload đã bị chặn.")
        return actual

    @staticmethod
    def private_body(metadata: dict) -> dict:
        if not isinstance(metadata, dict) or metadata.get("audience") not in {"made_for_kids", "not_made_for_kids"}:
            raise ValueError("Cần khai báo rõ đối tượng trẻ em trước khi upload.")
        if metadata.get("mode", "immediate") != "immediate":
            raise ValueError("Phase 4A-API không tự chuyển hẹn giờ hoặc đăng công khai sang tải thử Riêng tư.")
        meta = normalize_metadata(metadata)
        title = meta["title"].strip()
        if not title or len(title) > 100:
            raise ValueError("Tiêu đề YouTube phải có 1–100 ký tự.")
        if len(meta["description"]) > 5000:
            raise ValueError("Mô tả YouTube vượt quá 5000 ký tự.")
        snippet = {"title": title, "description": meta["description"], "defaultLanguage": "ja"}
        tags = [tag.strip() for tag in meta["tags"].split(",") if tag.strip()]
        if tags:
            snippet["tags"] = tags
        return {"snippet": snippet, "status": {"privacyStatus": "private",
                "selfDeclaredMadeForKids": meta["audience"] == "made_for_kids"}}

    @staticmethod
    def planned_schedule_status(metadata: dict) -> dict:
        """Prepare an API status for a later, explicitly gated scheduling phase."""
        meta = normalize_metadata(metadata)
        if meta["mode"] != "scheduled":
            raise ValueError("Chưa chọn chế độ hẹn giờ.")
        return {"privacyStatus": "private",
                "publishAt": validate_schedule(meta["scheduled_local"], meta["timezone"])}

    def upload_private(self, credentials, expected_channel_id: str, source: Path, metadata: dict,
                       on_created, thumbnail: Path | None = None) -> dict:
        self.verify_exact_channel(credentials, expected_channel_id)
        source = Path(source)
        if not source.is_file() or source.suffix.lower() != ".mp4":
            raise ValueError("Không tìm thấy MP4 Full HD hợp lệ.")
        body = self.private_body(metadata)
        service = self.service_factory(credentials)
        try:
            media = self.media_factory(str(source), mimetype="video/mp4", resumable=True,
                                       chunksize=8 * 1024 * 1024)
            request = service.videos().insert(part="snippet,status", body=body, media_body=media,
                                              notifySubscribers=False)
            response = None
            while response is None:
                _, response = request.next_chunk()
        except Exception as exc:
            raise safe_api_error(exc) from exc
        video_id = response.get("id") if isinstance(response, dict) else None
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            raise YouTubeAPIError("Upload có thể đã thành công nhưng API chưa xác nhận video ID; kiểm tra Studio trước khi thử lại.")
        on_created(video_id)  # Persist immediately; never re-upload after a thumbnail failure.
        thumbnail_error = ""
        if thumbnail:
            thumb = Path(thumbnail)
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(thumb.suffix.lower())
            if not mime or not thumb.is_file() or thumb.stat().st_size > 50 * 1024 * 1024:
                thumbnail_error = "Thumbnail phải là JPEG/PNG tối đa 50 MB."
            else:
                try:
                    image = self.media_factory(str(thumb), mimetype=mime, resumable=False)
                    service.thumbnails().set(videoId=video_id, media_body=image).execute()
                except Exception as exc:
                    thumbnail_error = str(safe_api_error(exc))
        confirmed = self.reconcile_known_video(credentials, expected_channel_id, video_id)
        if confirmed["privacy"] != "private":
            raise YouTubeAPIError("YouTube chưa xác nhận video ở chế độ Private; cần kiểm tra trong Studio.")
        return {"video_id": video_id, "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "privacy": "private", "thumbnail_error": thumbnail_error}

    def reconcile_known_video(self, credentials, expected_channel_id: str, video_id: str) -> dict:
        self.verify_exact_channel(credentials, expected_channel_id)
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError("Video ID không hợp lệ.")
        try:
            result = self.service_factory(credentials).videos().list(part="snippet,status", id=video_id).execute()
        except Exception as exc:
            raise safe_api_error(exc) from exc
        items = result.get("items", []) if isinstance(result, dict) else []
        if len(items) != 1 or items[0].get("snippet", {}).get("channelId") != expected_channel_id:
            raise YouTubeAPIError("Không xác minh được video thuộc kênh đã chọn.")
        return {"video_id": video_id, "privacy": items[0].get("status", {}).get("privacyStatus", "unknown")}


class OwnerOAuth:
    def __init__(self, config_path: Path, vault: CredentialVault, api: YouTubeAPIClient):
        self.config_path = Path(config_path)
        self.vault = vault
        self.api = api

    def authorize(self) -> dict:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            config = validate_client_config(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise YouTubeAPIError("Không đọc được JSON OAuth Desktop app trong cấu hình máy.") from exc
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
        try:
            credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                access_type="offline", prompt="consent", timeout_seconds=300,
                authorization_prompt_message=None)
        except Exception as exc:
            raise YouTubeAPIError("Google OAuth không hoàn tất; kiểm tra tài khoản, test user và consent screen.") from exc
        require_scopes(credentials, initial_grant=True)
        if not getattr(credentials, "refresh_token", None):
            raise YouTubeAPIError("Google không cấp refresh token; hãy kiểm tra consent hoặc thu hồi grant cũ rồi kết nối lại.")
        channel = self.api.verify_channel(credentials)
        reference = uuid4().hex
        self.vault.save(reference, credentials)
        return {**channel, "connection_id": uuid4().hex, "credential_ref": reference, "scopes": list(SCOPES),
                "authorization_status": "authorized",
                "last_checked_at": datetime.now(timezone.utc).isoformat()}

    def credentials_for(self, reference: str):
        credentials = self.vault.load(reference)
        require_scopes(credentials)
        if not credentials.valid:
            try:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            except Exception as exc:
                raise YouTubeAPIError("OAuth token hết hạn hoặc đã bị thu hồi; chủ kênh cần ủy quyền lại.") from exc
            self.vault.save(reference, credentials)
        return credentials

    def revoke(self, reference: str) -> None:
        credentials = self.vault.load(reference)
        token = getattr(credentials, "refresh_token", None) or getattr(credentials, "token", None)
        if token:
            request = URLRequest("https://oauth2.googleapis.com/revoke",
                                 data=urlencode({"token": token}).encode("utf-8"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 method="POST")
            try:
                with urlopen(request, timeout=20) as response:
                    if response.status != 200:
                        raise YouTubeAPIError("Google chưa xác nhận thu hồi OAuth; credential vẫn được giữ.")
            except Exception as exc:
                raise YouTubeAPIError("Không thu hồi được OAuth với Google; kiểm tra mạng rồi thử lại.") from exc
        self.vault.delete(reference)
