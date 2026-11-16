"""One-shot Chrome Native Messaging host; no sockets, shell commands or media access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys

from .studio_connector import MAX_MESSAGE_BYTES, StudioConnectorStore


def handle(payload: bytes, config_path: Path) -> dict:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Thông điệp quá lớn.")
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict) or set(message) - {
            "type", "schema", "page_url", "page_url_after", "channels", "switcher_open"}:
        raise ValueError("Thông điệp có trường không được phép.")
    scan = StudioConnectorStore(config_path).save_message(message)
    return {"ok": True, "count": len(scan["channels"]), "scanned_at": scan["scanned_at"]}


def main() -> None:
    source = sys.stdin.buffer
    destination = sys.stdout.buffer
    header = source.read(4)
    if len(header) != 4:
        return
    size = struct.unpack("<I", header)[0]
    try:
        if size > MAX_MESSAGE_BYTES:
            raise ValueError("Thông điệp quá lớn.")
        payload = source.read(size)
        if len(payload) != size:
            raise ValueError("Thông điệp bị ngắt giữa chừng.")
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "TEStudio"
        response = handle(payload, base / "settings.json")
    except (ValueError, UnicodeError, OSError) as exc:
        response = {"ok": False, "error": str(exc)}
    encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
    destination.write(struct.pack("<I", len(encoded)) + encoded)
    destination.flush()


if __name__ == "__main__":
    main()
