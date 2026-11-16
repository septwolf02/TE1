"""Read-only YouTube Studio channel observations from a Chrome native host.

Studio DOM observations are not OAuth authorization or upload permission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

from te.project.manager import _write_json
from .channel_probe import channel_id_from_url

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CHANNELS = 100
STALE_SECONDS = 24 * 60 * 60


def _studio_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname == "studio.youtube.com" and
                parsed.port is None and not parsed.username and not parsed.password)
    except ValueError:
        return False


def validate_scan(message: object) -> dict:
    if not isinstance(message, dict) or message.get("type") != "scan_results" or message.get("schema") != 1:
        raise ValueError("Thông điệp Studio Connector không hợp lệ.")
    page_url = message.get("page_url")
    if not _studio_url(page_url) or not isinstance(message.get("channels"), list) or len(message["channels"]) > MAX_CHANNELS:
        raise ValueError("Nguồn trang hoặc danh sách kênh không hợp lệ.")
    if message.get("page_url_after") != page_url:
        raise ValueError("Trang Studio đã đổi trong lúc quét; kết quả bị hủy.")
    active_id = channel_id_from_url(page_url)
    manual_name = message.get("manual_name")
    if manual_name is not None:
        if (not active_id or not isinstance(manual_name, str) or not manual_name.strip() or
                len(manual_name.strip()) > 160 or any(ord(char) < 32 for char in manual_name)):
            raise ValueError("Tên nhập tay không hợp lệ hoặc URL Studio không có Channel ID.")
        manual_name = manual_name.strip()
    raw_diagnostics = message.get("diagnostics")
    if raw_diagnostics is not None:
        if (not isinstance(raw_diagnostics, dict) or set(raw_diagnostics) !=
                {"active_id_found", "active_name_found", "switcher_detected", "switcher_entries",
                 "switcher_names", "verified_identities"} or
                any(type(raw_diagnostics[key]) is not bool for key in
                    ("active_id_found", "active_name_found", "switcher_detected")) or
                any(type(raw_diagnostics[key]) is not int or not 0 <= raw_diagnostics[key] <= MAX_CHANNELS
                    for key in ("switcher_entries", "switcher_names", "verified_identities")) or
                raw_diagnostics["active_id_found"] != bool(active_id)):
            raise ValueError("Chẩn đoán Studio Connector không hợp lệ.")
    records = []
    seen_ids: dict[str, int] = {}
    seen_names = set()
    for raw in message["channels"]:
        if not isinstance(raw, dict):
            raise ValueError("Bản ghi kênh không hợp lệ.")
        name = raw.get("name")
        url = raw.get("source_url")
        if not isinstance(name, str) or len(name.strip()) > 160 or not _studio_url(url):
            raise ValueError("Tên hoặc chứng cứ URL kênh không hợp lệ.")
        name = name.strip()
        channel_id = channel_id_from_url(url)
        claimed_id = raw.get("id") or ""
        if claimed_id != (channel_id or ""):
            raise ValueError("Channel ID không khớp URL Studio được quan sát.")
        if channel_id:
            if channel_id in seen_ids:
                previous = records[seen_ids[channel_id]]
                if name and not previous["name"]:
                    previous.update(name=name, status="identity_verified", source_url=url,
                                    evidence="visible_studio_link", name_source="studio_switcher_dom")
                continue
            seen_ids[channel_id] = len(records)
        else:
            # A name without a URL-shaped ID is only a candidate, even if repeated.
            if not name or name in seen_names:
                continue
            seen_names.add(name)
        records.append({"id": channel_id or "", "name": name,
                        "status": "identity_verified" if channel_id and name else
                                  "identity_incomplete" if channel_id else "discovered",
                        "permission_status": "permission_unverified",
                        "source_url": url, "evidence": "visible_studio_link" if channel_id else "visible_switcher_name",
                        "id_source": "studio_url" if channel_id and channel_id == active_id else
                                     "studio_link" if channel_id else "missing",
                        "name_source": "studio_identity_dom" if name and channel_id == active_id else
                                       "studio_switcher_dom" if name else "missing",
                        "active": bool(channel_id and channel_id == active_id),
                        "connection_type": "studio_connector"})
    if manual_name is not None:
        active = next((item for item in records if item["id"] == active_id and item["active"]), None)
        if active is None or active["name"]:
            raise ValueError("Chỉ nhập tên tay khi ID đang mở đã xác nhận nhưng tên DOM còn thiếu.")
        active.update(name=manual_name, status="identity_user_named", name_source="user_supplied")
    scanned_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        record["last_seen_at"] = scanned_at
    diagnostics = {"active_id_found": bool(active_id),
                   "active_name_found": raw_diagnostics["active_name_found"] if raw_diagnostics else
                                        any(item["active"] and item["name"] and
                                            item["name_source"] != "user_supplied" for item in records),
                   "switcher_detected": message.get("switcher_open") is True,
                   "switcher_entries": raw_diagnostics["switcher_entries"] if raw_diagnostics else 0,
                   "switcher_names": raw_diagnostics["switcher_names"] if raw_diagnostics else 0,
                   "verified_identities": sum(item["status"] == "identity_verified" for item in records)}
    return {"schema": 1, "page_url": page_url, "active_channel_id": active_id or "",
            "complete_channel_list": False, "switcher_open": message.get("switcher_open") is True,
            "channels": records, "diagnostics": diagnostics, "scanned_at": scanned_at}


class StudioConnectorStore:
    def __init__(self, config_path: Path):
        self.path = Path(config_path).parent / "studio-connector" / "scan.json"

    def save_message(self, message: object) -> dict:
        result = validate_scan(message)
        result["latest_verified_count"] = sum(item["status"] == "identity_verified" for item in result["channels"])
        result["latest_user_named_count"] = sum(item["status"] == "identity_user_named" for item in result["channels"])
        result["latest_id_count"] = sum(bool(item["id"]) for item in result["channels"])
        result["latest_active_status"] = next((item["status"] for item in result["channels"] if item["active"]), "")
        if self.path.exists():
            old = self.load()
            seen = {item["id"] for item in result["channels"] if item["id"]}
            by_id = {item["id"]: index for index, item in enumerate(result["channels"]) if item["id"]}
            for item in old["channels"]:
                index = by_id.get(item.get("id"))
                if index is not None and result["channels"][index]["status"] in \
                        {"identity_incomplete", "identity_user_named"} and item.get("name") and \
                        (item.get("status") == "identity_verified" or
                         item.get("status") == "identity_user_named" and
                         result["channels"][index]["status"] == "identity_incomplete"):
                    # A later name-less view must not erase a previously observed name for the same URL ID.
                    result["channels"][index] = item
            result["channels"].extend(item for item in old["channels"]
                if item.get("id") and item.get("id") not in seen and
                item.get("evidence") == "visible_studio_link")
        _write_json(self.path, result)
        return result

    def load(self, *, now: datetime | None = None) -> dict:
        if not self.path.exists():
            return {"channels": [], "scanned_at": "", "complete_channel_list": False}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("channels"), list):
            raise ValueError("Bản quét Studio Connector không hợp lệ.")
        result = dict(data)
        now = now or datetime.now(timezone.utc)
        def is_stale(value):
            try:
                seen = datetime.fromisoformat(value)
                return seen.tzinfo is None or (now - seen).total_seconds() > STALE_SECONDS
            except (TypeError, ValueError):
                return True
        result["stale"] = is_stale(data.get("scanned_at"))
        result["channels"] = [dict(item, status="unavailable" if is_stale(item.get("last_seen_at")) else item["status"])
                              for item in data["channels"]]
        return result
