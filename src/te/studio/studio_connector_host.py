"""One-shot Chrome Native Messaging host; no sockets, shell commands or media access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
from datetime import datetime, timezone

from .studio_connector import MAX_MESSAGE_BYTES, StudioConnectorStore
from .studio_upload_bridge import StudioUploadBridge


def handle(payload: bytes, config_path: Path) -> dict:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Thông điệp quá lớn.")
    message = json.loads(payload.decode("utf-8"))
    if isinstance(message, dict) and message == {"type":"upload_get", "schema":1}:
        return {"ok":True, "request":StudioUploadBridge(config_path).pending()}
    if isinstance(message, dict) and set(message) == {"type", "schema", "request_id"} and \
            message.get("type") == "upload_state" and message.get("schema") == 1 and \
            isinstance(message.get("request_id"), str):
        return {"ok":True, **StudioUploadBridge(config_path).request_state(message["request_id"])}
    if isinstance(message, dict) and set(message) == {"type", "schema", "request_id"} and \
            message.get("type") == "upload_resume" and message.get("schema") == 1 and \
            isinstance(message.get("request_id"), str):
        return {"ok":True, "task":StudioUploadBridge(config_path).resume(message["request_id"])}
    if message == {"type":"upload_reconcile_get", "schema":1}:
        return {"ok":True, "request":StudioUploadBridge(config_path).reconcile_candidate()}
    if isinstance(message, dict) and set(message) == {"type", "schema", "request_id", "page_url",
            "video_id", "visibility"} and message.get("type") == "upload_reconcile" and \
            message.get("schema") == 1 and all(isinstance(message.get(key), str) for key in
            ("request_id", "page_url", "video_id", "visibility")):
        return {"ok":True, **StudioUploadBridge(config_path).reconcile(message["request_id"],
            message["page_url"], message["video_id"], message["visibility"])}
    if isinstance(message, dict) and set(message) == {"type", "schema", "request_id", "step"} and \
            message.get("type") == "upload_progress" and message.get("schema") == 1 and \
            isinstance(message.get("request_id"), str) and isinstance(message.get("step"), str):
        StudioUploadBridge(config_path).progress(message["request_id"], message["step"])
        return {"ok":True}
    if isinstance(message, dict) and set(message) == {"type", "schema", "request_id", "page_url",
            "video_id", "visibility"} and message.get("type") == "upload_complete" and \
            message.get("schema") == 1 and all(isinstance(message.get(key), str) for key in
            ("request_id", "page_url", "video_id", "visibility")):
        return {"ok":True, **StudioUploadBridge(config_path).complete(message["request_id"],
            message["page_url"], message["video_id"], message["visibility"])}
    if isinstance(message, dict) and message.get("schema") == 1 and message.get("type") in {
            "upload_claim", "upload_started", "upload_selected", "upload_failed"}:
        kind = message["type"]
        required = {"upload_claim":{"type", "schema", "request_id", "page_url", "page_url_after"},
                    "upload_started":{"type", "schema", "request_id"},
                    "upload_selected":{"type", "schema", "request_id", "filename", "bytes"},
                    "upload_failed":{"type", "schema", "request_id", "reason"}}[kind]
        if (set(message) != required and not (kind == "upload_failed" and
                set(message) == required | {"diagnostics"})) or not isinstance(message.get("request_id"), str):
            raise ValueError("Yêu cầu upload Connector có trường không hợp lệ.")
        bridge = StudioUploadBridge(config_path)
        if kind == "upload_claim":
            return {"ok":True, "file":bridge.claim(message["request_id"], message["page_url"],
                                                     message["page_url_after"])}
        if kind == "upload_started":
            bridge.started(message["request_id"])
        elif kind == "upload_selected":
            bridge.file_selected(message["request_id"], message["filename"], message["bytes"])
        else:
            if message["reason"] not in {"missing_input", "debugger_failure", "tab_closed",
                                         "channel_mismatch", "chrome_disconnected", "receipt_mismatch",
                                         "studio_no_reaction", "private_unverified"}:
                raise ValueError("Mã lỗi upload không hợp lệ.")
            diagnostics = message.get("diagnostics", {})
            if not isinstance(diagnostics, dict) or set(diagnostics) - {
                    "browser_step", "dialog_open", "candidate_count", "rejected", "cdp_attached"} or \
                    not isinstance(diagnostics.get("browser_step", ""), str) or \
                    len(diagnostics.get("browser_step", "")) > 40 or \
                    type(diagnostics.get("dialog_open", False)) is not bool or \
                    type(diagnostics.get("cdp_attached", False)) is not bool or \
                    type(diagnostics.get("candidate_count", 0)) is not int or \
                    not 0 <= diagnostics.get("candidate_count", 0) <= 100 or \
                    not isinstance(diagnostics.get("rejected", []), list) or \
                    len(diagnostics.get("rejected", [])) > 10 or \
                    any(not isinstance(value, str) or len(value) > 40
                        for value in diagnostics.get("rejected", [])):
                raise ValueError("Chẩn đoán upload Connector không hợp lệ.")
            bridge.fail(message["request_id"], message["reason"], diagnostics)
        return {"ok":True}
    if isinstance(message, dict) and message == {"type": "selection_get", "schema": 1}:
        return {"ok": True, "request": StudioConnectorStore(config_path).selection_request()}
    if (isinstance(message, dict) and message.get("type") == "selection_confirm" and
            set(message) == {"type", "schema", "request_id", "page_url", "page_url_after", "observed_name"} and
            message.get("schema") == 1):
        record = StudioConnectorStore(config_path).confirm_selection(
            message["request_id"], message["page_url"], message["page_url_after"],
            message["observed_name"])
        return {"ok": True, "key": record["key"], "channel_id": record["id"],
                "id_source": record["id_source"]}
    if isinstance(message, dict) and message == {"type": "catalog_get", "schema": 1}:
        records = StudioConnectorStore(config_path).catalog_records()
        return {"ok": True, "records": records, "count": len(records)}
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
