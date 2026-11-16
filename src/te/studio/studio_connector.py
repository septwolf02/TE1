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
                                    evidence="visible_studio_link")
                continue
            seen_ids[channel_id] = len(records)
        else:
            # A name without a URL-shaped ID is only a candidate, even if repeated.
            if not name or name in seen_names:
                continue
            seen_names.add(name)
        records.append({"id": channel_id or "", "name": name,
                        "status": "identity_verified" if channel_id and name else "discovered",
                        "permission_status": "permission_unverified",
                        "source_url": url, "evidence": "visible_studio_link" if channel_id else "visible_switcher_name",
                        "active": bool(channel_id and channel_id == active_id),
                        "connection_type": "studio_connector"})
    scanned_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        record["last_seen_at"] = scanned_at
    return {"schema": 1, "page_url": page_url, "active_channel_id": active_id or "",
            "complete_channel_list": False, "switcher_open": message.get("switcher_open") is True,
            "channels": records, "scanned_at": scanned_at}


class StudioConnectorStore:
    def __init__(self, config_path: Path):
        self.path = Path(config_path).parent / "studio-connector" / "scan.json"

    def save_message(self, message: object) -> dict:
        result = validate_scan(message)
        result["latest_verified_count"] = sum(item["status"] == "identity_verified" for item in result["channels"])
        if self.path.exists():
            old = self.load()
            seen = {item["id"] for item in result["channels"] if item["id"]}
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
