"""Safe handoff to YouTube Studio in the user's ordinary system browser.

Google rejected sign-in in the Playwright-controlled Chrome context. An Editor's
Studio channel permission does not grant YouTube API access, so there is no
verified automated publishing path here. Never treat opening a URL as proof of
identity, channel authorization, or upload capability.
"""
from __future__ import annotations

from pathlib import Path
import webbrowser

from .publisher import CHANNEL_ID


class StudioSessionExpired(RuntimeError):
    """Retained for compatibility with older Publisher callers."""


class StudioVerificationError(RuntimeError):
    pass


class YouTubeStudioBrowser:
    def __init__(self, profile_dir: Path):
        # The path identifies only the retired, isolated Playwright profile so
        # users may explicitly remove it. It is never opened or copied.
        self.profile_dir = Path(profile_dir)

    @staticmethod
    def _open(url: str) -> None:
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            raise StudioVerificationError("Không mở được trình duyệt hệ thống để vào YouTube Studio.") from exc
        if not opened:
            raise StudioVerificationError("Không mở được trình duyệt hệ thống để vào YouTube Studio.")

    def connect(self) -> str:
        self._open("https://studio.youtube.com/")
        return ("Đã mở YouTube Studio trong trình duyệt hệ thống. Đăng nhập trực tiếp tại đó; "
                "TE Studio chưa xác minh tài khoản, quyền kênh hoặc khả năng upload.")

    def verify_channel(self, channel_id: str) -> dict:
        if not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("ID kênh không đúng định dạng UC…")
        self._open(f"https://studio.youtube.com/channel/{channel_id}")
        return {"id": channel_id, "name": "", "upload_control_verified": False,
                "authorization_verified": False}

    def upload_private(self, job: dict) -> dict:
        raise StudioVerificationError(
            "Upload tự động tạm khóa: đăng nhập Google và quyền Editor trong trình duyệt hệ thống "
            "chưa thể xác minh an toàn từ TE Studio. Hãy tải video thủ công trong YouTube Studio.")
