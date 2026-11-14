"""Read-only Windows UIA feasibility probe for an explicitly chosen Studio window.

This is diagnostic, not channel discovery. UIA does not promise that Studio's
switcher exposes channel URLs or upload permissions. It never clicks, types,
changes tabs, reads browser profile files, or saves candidate channels.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Callable
from urllib.parse import urlparse

from .publisher import CHANNEL_ID

_STUDIO_TITLE = "YouTube Studio"
_CHANNEL_PATH = re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})(?:/|$)")


def channel_id_from_url(value: object) -> str | None:
    """Accept only a channel URL on the exact Studio host."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value.strip())
        valid_host = parsed.hostname == "studio.youtube.com" and parsed.port is None
    except ValueError:
        return None
    if parsed.scheme != "https" or not valid_host:
        return None
    match = _CHANNEL_PATH.match(parsed.path)
    return match.group(1) if match and CHANNEL_ID.fullmatch(match.group(1)) else None


def parse_visible_links(items: list[tuple[str, str]]) -> list[dict]:
    """Extract URL-anchored candidates; names alone never establish identity."""
    by_id: dict[str, set[str]] = {}
    for name, value in items:
        channel_id = channel_id_from_url(value)
        if channel_id:
            by_id.setdefault(channel_id, set()).add(name.strip() if isinstance(name, str) else "")
    return [{"id": channel_id, "names": sorted(names - {""}), "state": "unverified"}
            for channel_id, names in sorted(by_id.items())]


def _desktop():
    if os.name != "nt":
        raise RuntimeError("Probe UIA chỉ chạy trên Windows.")
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("Cài probe UIA bằng: .\\.venv\\Scripts\\python.exe -m pip install -e '.[publisher-probe]'.") from exc
    return Desktop(backend="uia")


def _title(window) -> str:
    return window.window_text() or ""


def list_studio_windows(desktop=None) -> list[dict]:
    desktop = desktop or _desktop()
    windows = []
    for window in desktop.windows():
        title = _title(window)
        if _STUDIO_TITLE in title:
            windows.append({"hwnd": int(window.handle), "title": title})
    return windows


def _value(element) -> str:
    try:
        value = element.get_value()
    except Exception:  # UIA providers may reject ValuePattern on individual elements.
        return ""
    return value if isinstance(value, str) else ""


def probe_studio_window(hwnd: int, desktop=None, cancelled: Callable[[], bool] | None = None) -> dict:
    desktop = desktop or _desktop()
    try:
        window = desktop.window(handle=hwnd).wrapper_object()
    except Exception as exc:
        raise ValueError("Không tìm thấy cửa sổ Studio đã chọn; hãy chạy --list lại.") from exc
    if _STUDIO_TITLE not in _title(window):
        raise ValueError("Cửa sổ được chọn không hiển thị YouTube Studio.")
    if cancelled and cancelled():
        raise RuntimeError("Đã hủy quét UIA.")
    # Read only URL-shaped Edit values. Do not retain other Studio inputs.
    addresses = set()
    for element in window.descendants(control_type="Edit"):
        if cancelled and cancelled():
            raise RuntimeError("Đã hủy quét UIA.")
        value = _value(element)
        if value.startswith("https://studio.youtube.com/"):
            addresses.add(value)
    active = {channel_id_from_url(value) for value in addresses}
    active.discard(None)
    if len(active) != 1:
        raise ValueError("Không xác nhận được một channel ID duy nhất từ URL cửa sổ Studio.")
    # Hyperlink ValuePattern is optional. A menu name without an ID is not a channel.
    links = []
    for element in window.descendants(control_type="Hyperlink"):
        if cancelled and cancelled():
            raise RuntimeError("Đã hủy quét UIA.")
        links.append((element.element_info.name or "", _value(element)))
    candidates = parse_visible_links(links)
    return {"hwnd": hwnd, "active_channel_id": active.pop(),
            "url_anchored_candidates": candidates, "verified_upload_permission": False,
            "complete_channel_list": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only YouTube Studio UIA feasibility probe")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List visible Studio windows and HWNDs")
    group.add_argument("--hwnd", type=int, help="Inspect one Studio window by its HWND")
    args = parser.parse_args()
    try:
        result = list_studio_windows() if args.list else probe_studio_window(args.hwnd)
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
