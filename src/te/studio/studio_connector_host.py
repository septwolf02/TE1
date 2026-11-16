"""One-shot Chrome Native Messaging host; no sockets, shell commands or media access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
from datetime import datetime, timezone

from .studio_connector import MAX_MESSAGE_BYTES, StudioConnectorStore


def handle(payload: bytes, config_path: Path) -> dict:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Thông điệp quá lớn.")
    message = json.loads(payload.decode("utf-8"))
    if isinstance(message, dict) and message.get("type") == "catalog_sync":
        if set(message) != {"type", "schema", "records"}:
            raise ValueError("Thông điệp catalog có trường không được phép.")
        catalog = StudioConnectorStore(config_path).save_catalog(message)
        return {"ok":True, "count":len(catalog["channels"]), "scanned_at":catalog["scanned_at"]}
    if not isinstance(message, dict) or set(message) - {
            "type", "schema", "page_url", "page_url_after", "channels", "switcher_open", "diagnostics",
            "manual_name"}:
        raise ValueError("Thông điệp có trường không được phép.")
    scan = StudioConnectorStore(config_path).save_message(message)
    return {"ok": True, "count": len(scan["channels"]),
            "verified_count": scan["latest_verified_count"],
            "user_named_count": scan["latest_user_named_count"],
            "id_count": scan["latest_id_count"],
            "active_status": scan["latest_active_status"],
            "diagnostics": scan["diagnostics"],
            "scanned_at": scan["scanned_at"]}


def _write_frame(destination, message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    destination.write(struct.pack("<I", len(encoded)) + encoded)
    destination.flush()


def _log(base: Path, stage: str, code: str) -> None:
    try:
        path = base / "studio-connector" / "connector.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now(timezone.utc).isoformat()} python {stage} {code}\n")
    except OSError:
        pass


def main() -> None:
    source = sys.stdin.buffer
    destination = sys.stdout.buffer
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "TEStudio"
    stage = "reading"
    try:
        header = source.read(4)
        if len(header) != 4:
            raise ValueError("Thiếu header Native Messaging.")
        size = struct.unpack("<I", header)[0]
        if size > MAX_MESSAGE_BYTES:
            raise ValueError("Thông điệp quá lớn.")
        payload = source.read(size)
        if len(payload) != size:
            raise ValueError("Thông điệp bị ngắt giữa chừng.")
        stage = "saving"
        _write_frame(destination, {"stage": "saving"})
        response = handle(payload, base / "settings.json")
        _log(base, "completed", "ok")
    except (ValueError, UnicodeError, OSError) as exc:
        _log(base, stage, type(exc).__name__)
        response = {"ok": False, "error": str(exc)}
    except Exception as exc:
        _log(base, stage, type(exc).__name__)
        response = {"ok": False, "error": "Native Host gặp lỗi nội bộ; xem connector.log và thử lại."}
    _write_frame(destination, response)


if __name__ == "__main__":
    main()
