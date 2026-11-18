"""Read-only YouTube Studio channel observations from a Chrome native host.

Studio DOM observations are not OAuth authorization or upload permission.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from te.project.manager import _write_json
from .channel_probe import channel_id_from_url
from .publisher import CHANNEL_ID

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CHANNELS = 100
STALE_SECONDS = 24 * 60 * 60
HANDLE = re.compile(r"^@[\w.\-]{2,100}$", re.UNICODE)


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
        self.selection_path = self.path.with_name("selection.json")

    def create_selection_request(self, key: str, *, now: datetime | None = None) -> dict:
        record = next((item for item in self.catalog_records() if item["key"] == key), None)
        if record is None:
            raise ValueError("Kênh Studio không còn trong catalog; hãy tải lại danh sách.")
        now = now or datetime.now(timezone.utc)
        request = {"schema": 1, "request_id": uuid4().hex, "key": key,
                   "id": record.get("id", ""), "id_source": record.get("id_source", "missing"),
                   "name": record.get("name") or record.get("display_name") or "",
                   "handle": record.get("handle", ""), "status": "pending",
                   "created_at": now.isoformat(),
                   "expires_at": (now + timedelta(minutes=2)).isoformat()}
        _write_json(self.selection_path, request)
        return request

    def selection_request(self, *, now: datetime | None = None) -> dict:
        if not self.selection_path.exists():
            return {"pending": False}
        request = json.loads(self.selection_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or request.get("schema") != 1:
            raise ValueError("Yêu cầu xác minh Studio không hợp lệ.")
        now = now or datetime.now(timezone.utc)
        if request.get("status") == "pending" and now >= datetime.fromisoformat(request["expires_at"]):
            return {"pending": False, "status": "expired", "request_id": request["request_id"]}
        return dict(request, pending=request.get("status") == "pending")

    def confirm_selection(self, request_id: str, page_url: str, page_url_after: str,
                          observed_name: str = "", *, now: datetime | None = None) -> dict:
        request = self.selection_request(now=now)
        if not request.get("pending") or request.get("request_id") != request_id:
            raise ValueError("Yêu cầu xác minh đã hết hạn hoặc không khớp; hãy chọn lại trong TE.")
        if not _studio_url(page_url) or page_url != page_url_after:
            raise ValueError("URL Studio không hợp lệ hoặc đã thay đổi khi xác minh.")
        active_id = channel_id_from_url(page_url)
        if not active_id:
            raise ValueError("URL Studio đang mở không có Channel ID UC hợp lệ.")
        if not isinstance(observed_name, str) or len(observed_name) > 160 or any(ord(c) < 32 for c in observed_name):
            raise ValueError("Tên kênh quan sát được không hợp lệ.")
        data = self._raw_data()
        rows = [item for item in data["channels"] if item.get("key") == request["key"]]
        if len(rows) != 1:
            raise ValueError("Bản ghi kênh đã thay đổi; hãy chọn lại trong TE.")
        record = rows[0]
        if (record.get("id", "") != request["id"] or
                record.get("id_source", "missing") != request["id_source"]):
            raise ValueError("Channel ID đã thay đổi sau khi tạo yêu cầu; hãy chọn lại trong TE.")
        if record.get("id") and record["id"] != active_id:
            raise ValueError("Channel ID trong URL Studio khác ID của kênh đã chọn; không xác minh.")
        if any(item is not record and item.get("id") == active_id for item in data["channels"]):
            raise ValueError("Channel ID trong URL Studio đã thuộc bản ghi kênh khác.")
        now = now or datetime.now(timezone.utc)
        evidence_url = f"https://studio.youtube.com/channel/{active_id}"
        record.update(id=active_id, id_source="studio_url_user_confirmed",
                      status="identity_user_confirmed", source_url=evidence_url,
                      last_seen_at=now.isoformat(), identity_verified_at=now.isoformat(),
                      identity_request_id=request_id,
                      identity_evidence_url=evidence_url,
                      identity_name_observed=observed_name.strip(),
                      permission_status="permission_unverified", upload_status="upload_unverified")
        _write_json(self.path, data)
        _write_json(self.selection_path, dict(request, status="confirmed", confirmed_id=active_id,
                                              confirmed_name=observed_name.strip(),
                                              confirmed_at=now.isoformat()))
        return dict(record)

    def _raw_data(self) -> dict:
        if not self.path.exists():
            return {"schema": 1, "channels": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("channels"), list):
            raise ValueError("Catalog Studio Connector không hợp lệ.")
        return data

    def catalog_records(self) -> list[dict]:
        """Return stored catalog rows without converting old observations to stale UI status."""
        private_evidence = {"identity_verified_at", "identity_request_id", "identity_evidence_url",
                            "identity_name_observed"}
        return [{key: value for key, value in item.items() if key not in private_evidence}
                for item in self._raw_data()["channels"]
                if isinstance(item, dict) and item.get("key")]

    def link_manual_id(self, key: str, channel_id: str) -> dict:
        """Keep discovery evidence intact; a typed ID is never Studio-verified."""
        if not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("Channel ID phải có dạng UC và 22 ký tự tiếp theo.")
        data = self._raw_data()
        matches = [item for item in data["channels"] if item.get("key") == key]
        if len(matches) != 1:
            raise ValueError("Không tìm thấy đúng một bản ghi kênh; hãy quét lại catalog.")
        record = matches[0]
        if record.get("id_source") in {"studio_url", "studio_link", "studio_url_user_confirmed"}:
            raise ValueError("ID đã có chứng cứ Studio; không thể sửa bằng nhập tay.")
        if any(item is not record and item.get("id") == channel_id for item in data["channels"]):
            raise ValueError("Channel ID này đã gắn với một bản ghi khác.")
        record.update(id=channel_id, id_source="user_supplied", status="identity_user_supplied",
                      permission_status="permission_unverified", upload_status="upload_unverified")
        data["catalog_synced_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(self.path, data)
        return dict(record)

    def save_message(self, message: object) -> dict:
        result = validate_scan(message)
        result["latest_verified_count"] = sum(item["status"] == "identity_verified" for item in result["channels"])
        result["latest_user_named_count"] = sum(item["status"] == "identity_user_named" for item in result["channels"])
        result["latest_id_count"] = sum(bool(item["id"]) for item in result["channels"])
        result["latest_active_status"] = next((item["status"] for item in result["channels"] if item["active"]), "")
        if self.path.exists():
            old = self._raw_data()
            seen = {item["id"] for item in result["channels"] if item["id"]}
            by_id = {item["id"]: index for index, item in enumerate(result["channels"]) if item["id"]}
            for item in old["channels"]:
                index = by_id.get(item.get("id"))
                if index is not None and item.get("identity_verified_at"):
                    result["channels"][index] = item
                    continue
                if index is not None and result["channels"][index]["status"] in \
                        {"identity_incomplete", "identity_user_named"} and item.get("name") and \
                        (item.get("status") == "identity_verified" or
                         item.get("status") == "identity_user_named" and
                         result["channels"][index]["status"] == "identity_incomplete"):
                    # A later name-less view must not erase a previously observed name for the same URL ID.
                    result["channels"][index] = item
            result["channels"].extend(item for item in old["channels"]
                if item.get("id") not in seen and
                (item.get("key") or item.get("evidence") == "visible_studio_link"))
        _write_json(self.path, result)
        return result

    def save_catalog(self, message: object) -> dict:
        """Merge a user-reviewed extension catalog without deleting older observations."""
        if (not isinstance(message, dict) or message.get("type") != "catalog_sync" or
                message.get("schema") != 1 or not isinstance(message.get("records"), list) or
                len(message["records"]) > MAX_CHANNELS):
            raise ValueError("Catalog Studio Connector không hợp lệ.")
        now = datetime.now(timezone.utc).isoformat()
        incoming = []
        ids = set()
        keys = set()
        for raw in message["records"]:
            if not isinstance(raw, dict) or set(raw) - {"key", "id", "handle", "discovered_name", "display_name",
                    "name_source", "handle_source", "id_source", "status", "permission_status", "upload_status",
                    "source_url", "evidence", "last_seen_at", "user_edited", "duplicate_name",
                    "duplicate_handle"}:
                raise ValueError("Bản ghi catalog có trường không được phép.")
            channel_id = raw.get("id", "")
            key = raw.get("key")
            handle = raw.get("handle", "")
            discovered = raw.get("discovered_name", "")
            display = raw.get("display_name", "")
            url = raw.get("source_url")
            name_source = raw.get("name_source", "missing")
            id_source = raw.get("id_source", "missing")
            handle_source = raw.get("handle_source", "missing")
            evidence = raw.get("evidence", "visible_switcher_item")
            if (not isinstance(channel_id, str) or not isinstance(key, str) or not 1 <= len(key) <= 160 or
                    key in keys or channel_id and channel_id in ids or
                    not isinstance(handle, str) or handle and not HANDLE.fullmatch(handle) or
                    any(not isinstance(value, str) or len(value) > 160 or any(ord(ch) < 32 for ch in value)
                        for value in (discovered, display)) or not _studio_url(url) or
                    (channel_id_from_url(url) or "") != ("" if id_source == "user_supplied" else channel_id) or
                    (id_source == "user_supplied" and not channel_id) or
                    (bool(channel_id) != (id_source != "missing")) or
                    not isinstance(raw.get("user_edited"), bool) or
                    raw.get("user_edited") and raw.get("name_source") != "user_edited" or
                    name_source not in {"missing", "studio_switcher_dom", "studio_identity_dom", "user_edited"} or
                    id_source not in {"missing", "studio_url", "studio_link", "studio_url_user_confirmed", "user_supplied"} or
                    handle_source not in {"missing", "studio_switcher_dom"} or
                    not isinstance(evidence, str) or len(evidence) > 80 or
                    evidence not in {"visible_switcher_item", "active_studio_url"} or
                    raw.get("permission_status") != "permission_unverified" or
                    raw.get("upload_status") != "upload_unverified"):
                raise ValueError("Tên, ID, handle hoặc chứng cứ catalog không hợp lệ.")
            if channel_id and not CHANNEL_ID.fullmatch(channel_id):
                raise ValueError("Channel ID không hợp lệ.")
            if not channel_id and not handle and not discovered:
                raise ValueError("Ứng viên thiếu tên, handle và ID.")
            keys.add(key)
            if channel_id:
                ids.add(channel_id)
            name_source = "user_edited" if raw["user_edited"] else name_source
            status = ("identity_user_supplied" if id_source == "user_supplied" else
                      "identity_user_confirmed" if channel_id and id_source == "studio_url_user_confirmed" else
                      "identity_verified" if channel_id and discovered else
                      "identity_user_named" if channel_id and display and raw["user_edited"] else
                      "identity_incomplete" if channel_id else "discovered")
            incoming.append({"key":key, "id":channel_id, "name":display, "display_name":display,
                "discovered_name":discovered, "handle":handle, "name_source":name_source,
                "handle_source":handle_source,
                "id_source":id_source, "status":status,
                "permission_status":"permission_unverified", "upload_status":"upload_unverified",
                "source_url":url, "evidence":evidence,
                "last_seen_at":now,
                "user_edited":raw["user_edited"],
                "duplicate_name":raw.get("duplicate_name") is True,
                "duplicate_handle":raw.get("duplicate_handle") is True,
                "connection_type":"studio_connector"})
        previous = self._raw_data()["channels"]
        previous_by_key = {item.get("key"): item for item in previous if item.get("key")}
        for item in incoming:
            old = previous_by_key.get(item["key"])
            if old:
                for field in ("identity_verified_at", "identity_request_id", "identity_evidence_url",
                              "identity_name_observed"):
                    if field in old:
                        item[field] = old[field]
            if old and old.get("id_source") == "user_supplied" and item["id_source"] != "studio_url_user_confirmed":
                item.update(id=old["id"], id_source="user_supplied", status="identity_user_supplied",
                            source_url=old["source_url"])
            elif old and old.get("id_source") == "user_supplied" and item["id"] != old["id"]:
                raise ValueError("ID xác minh không khớp ID nhập tay; hãy sửa bản ghi trong TE trước.")
            elif old and old.get("id_source") in {"studio_url", "studio_link", "studio_url_user_confirmed"}:
                if item["id"] and item["id"] != old["id"]:
                    raise ValueError("ID mới xung đột với ID có chứng cứ Studio.")
                if (not item["id"] or item["id_source"] == "user_supplied" or
                        old["id_source"] == "studio_url_user_confirmed" and
                        item["id_source"] != "studio_url_user_confirmed"):
                    item.update(id=old["id"], id_source=old["id_source"], status=old["status"],
                                source_url=old["source_url"])
        all_ids = [item["id"] for item in incoming if item["id"]]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Một Channel ID đang gắn với nhiều bản ghi khác nhau.")
        previous_by_id = {item["id"]: item for item in previous if item.get("id")}
        for item in incoming:
            old = previous_by_id.get(item["id"])
            if old and old.get("name_source") == "user_edited" and not item["user_edited"]:
                item.update(name=old["name"], display_name=old["name"],
                            name_source="user_edited", user_edited=True)
            elif old and old.get("name") and not item["name"]:
                item.update(name=old["name"], display_name=old["name"],
                            name_source=old.get("name_source", "missing"))
                if item["status"] == "identity_incomplete":
                    item["status"] = old.get("status", "identity_incomplete")
        by_key = {item.get("key") or ("id:" + item["id"] if item.get("id") else "") for item in incoming}
        incoming.extend(item for item in previous if
            (item.get("key") or ("id:" + item["id"] if item.get("id") else "")) not in by_key and
            (not item.get("id") or item["id"] not in ids))
        all_ids = [item["id"] for item in incoming if item.get("id")]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Một Channel ID đang gắn với nhiều bản ghi khác nhau.")
        result = {"schema":1, "channels":incoming, "scanned_at":now, "complete_channel_list":False,
                  "catalog_synced_at":now}
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
