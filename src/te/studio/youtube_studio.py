"""Explicit, headed YouTube Studio browser operations (optional Playwright)."""
from __future__ import annotations

from pathlib import Path
import re

from .publisher import CHANNEL_ID, VIDEO_ID


class StudioSessionExpired(RuntimeError):
    pass


class StudioVerificationError(RuntimeError):
    pass


class YouTubeStudioBrowser:
    def __init__(self, profile_dir: Path):
        self.profile_dir = Path(profile_dir)

    def _context(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Cần cài Playwright: pip install -e '.[gui,publisher]' và python -m playwright install chromium.") from exc
        playwright = sync_playwright().start()
        try:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir), headless=False, viewport={"width": 1280, "height": 850})
        except Exception:
            playwright.stop()
            raise
        return playwright, context

    def connect(self) -> str:
        """User signs in in a dedicated browser and closes it when finished."""
        playwright, context = self._context()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://studio.youtube.com/", wait_until="domcontentloaded")
            try:
                page.wait_for_event("close", timeout=600_000)
            except Exception:
                pass
            return "Đã đóng trình duyệt kết nối. Chọn ID kênh từ URL Studio rồi bấm Làm mới danh sách kênh."
        finally:
            context.close()
            playwright.stop()

    def verify_channel(self, channel_id: str) -> dict:
        if not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("ID kênh không đúng định dạng UC…")
        playwright, context = self._context()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return self._verify_page(page, channel_id)
        finally:
            context.close()
            playwright.stop()

    @staticmethod
    def _verify_page(page, channel_id: str) -> dict:
        page.goto(f"https://studio.youtube.com/channel/{channel_id}", wait_until="domcontentloaded")
        try:
            page.wait_for_url(re.compile(r"studio\.youtube\.com/channel/UC"), timeout=20000)
        except Exception as exc:
            raise StudioSessionExpired("Phiên Google/YouTube Studio đã hết hạn hoặc chưa đăng nhập.") from exc
        if f"/channel/{channel_id}" not in page.url:
            raise StudioVerificationError("Studio đã chuyển sang kênh khác; hãy kiểm tra tài khoản và quyền Editor.")
        create = page.locator("#create-icon, ytcp-button#create-icon, button[aria-label='Create']").first
        try:
            create.wait_for(state="visible", timeout=15000)
            create.click()
            upload = page.get_by_text(re.compile(r"Upload videos|Tải video lên|動画をアップロード", re.I)).first
            upload.wait_for(state="visible", timeout=10000)
            upload.click()
            page.locator("input[type=file]").first.wait_for(state="attached", timeout=15000)
        except Exception as exc:
            raise StudioVerificationError("Không xác minh được quyền mở Upload trong Studio cho kênh này.") from exc
        return {"id": channel_id, "name": "", "upload_control_verified": True}

    def upload_private(self, job: dict) -> dict:
        """Upload once, finish only with Private selected; unknown outcomes need review."""
        channel_id = job["channel_id"]
        metadata = job["metadata"]
        playwright, context = self._context()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            self._verify_page(page, channel_id)
            file_input = page.locator("input[type=file]").first
            file_input.wait_for(state="attached", timeout=20000)
            file_input.set_input_files(job["source"]["path"])
            dialog = page.locator("ytcp-uploads-dialog").first
            dialog.wait_for(state="visible", timeout=30000)
            title = dialog.locator("#title-textarea #textbox, #title-textarea [contenteditable=true]").first
            title.wait_for(state="visible", timeout=20000)
            title.fill(metadata["title"])
            description = dialog.locator("#description-textarea #textbox, #description-textarea [contenteditable=true]").first
            description.fill(metadata["description"])
            audience = ("VIDEO_MADE_FOR_KIDS" if metadata["audience"] == "made_for_kids"
                        else "VIDEO_NOT_MADE_FOR_KIDS")
            dialog.locator(f"tp-yt-paper-radio-button[name='{audience}']").click()
            for _ in range(3):
                dialog.locator("#next-button").click(timeout=3_600_000)
            private = dialog.locator("tp-yt-paper-radio-button[name='PRIVATE']")
            private.wait_for(state="visible", timeout=15000)
            private.click()
            if private.get_attribute("aria-checked") != "true":
                raise StudioVerificationError("Không xác nhận được chế độ Riêng tư; chưa bấm Lưu.")
            link = dialog.locator("a[href*='watch?v='], a[href*='youtu.be/']").first
            link.wait_for(state="visible", timeout=3_600_000)
            href = link.get_attribute("href") or ""
            match = re.search(r"(?:[?&]v=|youtu\.be/)([A-Za-z0-9_-]{11})", href)
            if not match or not VIDEO_ID.fullmatch(match.group(1)):
                raise StudioVerificationError("Đã bấm Lưu nhưng chưa xác nhận được video ID; kiểm tra Studio trước khi thử lại.")
            dialog.locator("#done-button").click(timeout=3_600_000)
            dialog.wait_for(state="hidden", timeout=60_000)
            return {"video_id": match.group(1), "video_url": f"https://www.youtube.com/watch?v={match.group(1)}",
                    "state": "private_test"}
        finally:
            context.close()
            playwright.stop()
