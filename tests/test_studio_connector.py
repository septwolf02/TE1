"""Native-host and Studio observation fixtures; never contacts Google or Chrome."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

from te.studio.publisher import PublisherStore, normalize_metadata
from te.studio.studio_connector import StudioConnectorStore, validate_scan
from te.studio.studio_connector_host import handle

A = "UC" + "a" * 22
B = "UC" + "b" * 22


def page(channel=A):
    return f"https://studio.youtube.com/channel/{channel}/videos/upload"


def message(channel=A, entries=None):
    url = page(channel)
    return {"type": "scan_results", "schema": 1, "page_url": url, "page_url_after": url,
            "switcher_open": True, "channels": entries if entries is not None else
            [{"id": channel, "name": "同じ名前", "source_url": url}]}


def test_verified_ids_duplicate_names_and_unresolved_candidates():
    entries = [{"id": A, "name": "", "source_url": page(A)},
               {"id": A, "name": "同じ名前", "source_url": page(A)},
               {"id": B, "name": "同じ名前", "source_url": page(B)},
               {"id": A, "name": "Duplicate", "source_url": page(A)},
               {"id": "", "name": "Editor candidate", "source_url": "https://studio.youtube.com/"}]
    scan = validate_scan(message(entries=entries))
    assert [item["id"] for item in scan["channels"]] == [A, B, ""]
    assert [item["status"] for item in scan["channels"]] == ["identity_verified", "identity_verified", "discovered"]
    assert all(item["permission_status"] == "permission_unverified" for item in scan["channels"])
    assert scan["complete_channel_list"] is False


def test_valid_active_url_without_name_keeps_identity_incomplete():
    scan = validate_scan(message(entries=[{"id": A, "name": "", "source_url": page(A)}]))
    assert scan["active_channel_id"] == A
    assert scan["channels"][0]["id"] == A
    assert scan["channels"][0]["status"] == "identity_incomplete"
    assert scan["diagnostics"]["active_id_found"] is True
    assert scan["diagnostics"]["active_name_found"] is False


def test_manual_name_is_bound_only_to_url_confirmed_active_id(tmp_path):
    raw = message(entries=[{"id": A, "name": "", "source_url": page(A)}])
    incomplete_reply = handle(json.dumps(raw).encode(), tmp_path / "settings.json")
    assert incomplete_reply["active_status"] == "identity_incomplete"
    result = validate_scan({**raw, "manual_name": "大谷翔平速報"})
    record = result["channels"][0]
    assert record["id"] == A and record["name"] == "大谷翔平速報"
    assert record["status"] == "identity_user_named"
    assert record["id_source"] == "studio_url" and record["name_source"] == "user_supplied"
    assert result["diagnostics"]["active_name_found"] is False
    with pytest.raises(ValueError, match="Chỉ nhập tên tay"):
        validate_scan({**message(), "manual_name": "Unrelated"})
    with pytest.raises(ValueError, match="URL Studio"):
        no_id = {**raw, "page_url": "https://studio.youtube.com/", "page_url_after": "https://studio.youtube.com/"}
        validate_scan({**no_id, "manual_name": "Unrelated"})
    with pytest.raises(ValueError, match="Chỉ nhập tên tay"):
        validate_scan({**message(A, [{"id": B, "name": "", "source_url": page(B)}]),
                       "manual_name": "Wrong channel"})


@pytest.mark.parametrize("change", [
    {"page_url_after": page(B)}, {"page_url": "https://evil.test/channel/" + A},
    {"channels": [{"id": B, "name": "Wrong", "source_url": page(A)}]},
    {"channels": [{"id": A, "name": "Wrong", "source_url": "https://evil.test/channel/" + A}]},
    {"type": "upload"},
])
def test_rejects_switches_untrusted_origins_and_fabricated_id(change):
    with pytest.raises(ValueError):
        validate_scan({**message(), **change})


def test_native_host_rejects_extra_commands_and_oversized_payload(tmp_path):
    config = tmp_path / "settings.json"
    invalid = {**message(), "execute": "calc.exe"}
    with pytest.raises(ValueError):
        handle(json.dumps(invalid).encode(), config)
    with pytest.raises(ValueError, match="quá lớn"):
        handle(b" " * (1024 * 1024 + 1), config)
    assert not StudioConnectorStore(config).path.exists()
    result = handle(json.dumps(message(), ensure_ascii=False).encode(), config)
    assert result["ok"] and result["count"] == 1 and result["verified_count"] == 1
    unresolved = message(entries=[{"id": "", "name": "Only a name", "source_url": "https://studio.youtube.com/"}])
    result = handle(json.dumps(unresolved).encode(), config)
    assert result["ok"] and result["count"] == 2 and result["verified_count"] == 0
    assert result["active_status"] == ""


def catalog_record(channel, name, *, handle="@sample", edited=False):
    return {"key":f"id:{channel}", "id":channel, "handle":handle,
        "discovered_name":name, "display_name":"Edited " + name if edited else name,
        "name_source":"user_edited" if edited else "studio_switcher_dom",
        "handle_source":"studio_switcher_dom", "id_source":"studio_link",
        "status":"identity_verified", "permission_status":"permission_unverified",
        "upload_status":"upload_unverified", "source_url":page(channel),
        "evidence":"visible_switcher_item", "last_seen_at":datetime.now(timezone.utc).isoformat(),
        "user_edited":edited, "duplicate_name":False, "duplicate_handle":False}


def test_bulk_catalog_sync_preserves_edits_old_channels_and_restart(tmp_path):
    config = tmp_path / "settings.json"
    store = StudioConnectorStore(config)
    store.save_message(message(A))
    first = {"type":"catalog_sync", "schema":1, "records":[
        catalog_record(A, "Same", edited=True), catalog_record(B, "Same")]}
    reply = handle(json.dumps(first, ensure_ascii=False).encode(), config)
    assert reply["ok"] and reply["count"] == 2
    records = {item["id"]:item for item in StudioConnectorStore(config).load()["channels"]}
    assert records[A]["name"] == "Edited Same" and records[A]["name_source"] == "user_edited"
    assert records[B]["name"] == "Same" and records[B]["status"] == "identity_verified"
    assert all(item["upload_status"] == "upload_unverified" for item in records.values())
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[catalog_record(A, "Fresh DOM name")]})
    records = {item["id"]:item for item in StudioConnectorStore(config).load()["channels"]}
    assert set(records) == {A, B}
    assert records[A]["name"] == "Edited Same" and records[A]["discovered_name"] == "Fresh DOM name"


def test_bulk_catalog_rejects_forged_id_without_data_loss(tmp_path):
    config = tmp_path / "settings.json"
    store = StudioConnectorStore(config)
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[catalog_record(A, "A")]})
    original = store.path.read_bytes()
    bad = {**catalog_record(B, "Wrong"), "source_url":page(A)}
    with pytest.raises(ValueError, match="catalog không hợp lệ"):
        store.save_catalog({"type":"catalog_sync", "schema":1, "records":[bad]})
    assert store.path.read_bytes() == original


def test_unresolved_duplicate_names_remain_distinct_candidates(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    base = {**catalog_record(A, "Same"), "id":"", "source_url":"https://studio.youtube.com/",
            "id_source":"missing", "status":"discovered", "handle":""}
    records = [{**base, "key":"candidate:one"}, {**base, "key":"candidate:two"}]
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":records})
    assert len(store.load()["channels"]) == 2
    assert {item["key"] for item in store.load()["channels"]} == {"candidate:one", "candidate:two"}


def test_manual_channel_id_correction_sync_and_studio_verification(tmp_path):
    config = tmp_path / "settings.json"
    store = StudioConnectorStore(config)
    candidate = {**catalog_record(A, "大谷翔平速報", edited=True), "key":"candidate:one",
                 "id":"", "id_source":"missing", "status":"discovered",
                 "source_url":"https://studio.youtube.com/"}
    other = {**catalog_record(B, "別のチャンネル"), "key":"candidate:two",
             "id":"", "id_source":"missing", "status":"discovered",
             "source_url":"https://studio.youtube.com/"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[candidate, other]})
    with pytest.raises(ValueError, match="Channel ID"):
        store.link_manual_id("candidate:one", "not-a-channel")
    store.link_manual_id("candidate:one", A)
    corrected = store.link_manual_id("candidate:one", B)
    assert corrected["id_source"] == "user_supplied" and corrected["status"] == "identity_user_supplied"
    assert corrected["name"] == "Edited 大谷翔平速報" and corrected["handle"] == "@sample"
    with pytest.raises(ValueError, match="đã gắn"):
        store.link_manual_id("candidate:two", B)
    # A stale Chrome draft cannot undo a correction made in TE.
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[candidate, other]})
    assert store.catalog_records()[0]["id"] == B
    fetched = handle(json.dumps({"type":"catalog_get", "schema":1}).encode(), config)
    assert fetched["records"][0]["id_source"] == "user_supplied"
    assert len(fetched["records"]) == 2
    verified = {**candidate, "id":B, "id_source":"studio_url_user_confirmed",
                "source_url":page(B), "status":"identity_user_confirmed"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[verified, other]})
    assert StudioConnectorStore(config).catalog_records()[0]["status"] == "identity_user_confirmed"
    assert StudioConnectorStore(config).catalog_records()[0]["name"] == "Edited 大谷翔平速報"


def test_manual_id_cannot_replace_verified_id_or_collide_with_old_record(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    verified = catalog_record(A, "Confirmed")
    candidate = {**catalog_record(B, "Candidate"), "key":"candidate:other", "id":"",
                 "id_source":"missing", "source_url":"https://studio.youtube.com/"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[verified, candidate]})
    with pytest.raises(ValueError, match="chứng cứ Studio"):
        store.link_manual_id(verified["key"], B)
    with pytest.raises(ValueError, match="đã gắn"):
        store.link_manual_id(candidate["key"], A)
    assert len(store.catalog_records()) == 2
    stale = {**verified, "id":"", "id_source":"missing",
             "source_url":"https://studio.youtube.com/"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[stale, candidate]})
    assert store.catalog_records()[0]["id"] == A
    malformed = {**candidate, "id":"UCshort", "id_source":"user_supplied"}
    with pytest.raises(ValueError, match="ID"):
        store.save_catalog({"type":"catalog_sync", "schema":1, "records":[malformed]})


def test_selection_request_confirms_url_without_granting_upload(tmp_path):
    config = tmp_path / "settings.json"
    store = StudioConnectorStore(config)
    first = catalog_record(A, "同じ名前", edited=True)
    second = {**catalog_record(B, "同じ名前"), "key": "candidate:second", "id": "",
              "id_source": "missing", "status": "discovered",
              "source_url": "https://studio.youtube.com/"}
    store.save_catalog({"type": "catalog_sync", "schema": 1, "records": [first, second]})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = store.create_selection_request("candidate:second", now=start)
    assert request["id"] == "" and request["name"] == "同じ名前"
    assert store.selection_request(now=start)["request_id"] == request["request_id"]
    with pytest.raises(ValueError, match="URL Studio"):
        store.confirm_selection(request["request_id"], "https://studio.youtube.com/",
                                "https://studio.youtube.com/", now=start)
    with pytest.raises(ValueError, match="bản ghi kênh khác"):
        store.confirm_selection(request["request_id"], page(A), page(A), now=start)
    with pytest.raises(ValueError, match="không khớp"):
        store.confirm_selection("0" * 32, page(B), page(B), now=start)
    result = store.confirm_selection(request["request_id"], page(B), page(B), "Studio B", now=start)
    assert result["id"] == B and result["id_source"] == "studio_url_user_confirmed"
    assert result["name"] == "同じ名前"
    assert result["permission_status"] == "permission_unverified"
    assert result["upload_status"] == "upload_unverified"
    saved = StudioConnectorStore(config).load(now=start)["channels"]
    assert saved[1]["identity_request_id"] == request["request_id"]
    assert saved[1]["identity_evidence_url"] == f"https://studio.youtube.com/channel/{B}"
    assert saved[1]["identity_name_observed"] == "Studio B"
    assert store.selection_request(now=start)["status"] == "confirmed"
    assert PublisherStore(config).data["jobs"] == []
    assert "identity_request_id" not in handle(json.dumps({"type":"catalog_get", "schema":1}).encode(), config)["records"][1]
    assert handle(json.dumps({"type":"selection_get", "schema":1}).encode(), config)["request"]["status"] == "confirmed"


def test_selection_manual_id_mismatch_expiry_replacement_and_resync(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    candidate = {**catalog_record(A, "Manual"), "key": "candidate:one", "id": "",
                 "id_source": "missing", "source_url": "https://studio.youtube.com/"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[candidate]})
    store.link_manual_id("candidate:one", A)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = store.create_selection_request("candidate:one", now=start)
    with pytest.raises(ValueError, match="khác ID"):
        store.confirm_selection(old["request_id"], page(B), page(B), now=start)
    with pytest.raises(ValueError, match="URL Studio"):
        store.confirm_selection(old["request_id"], page(A), page(B), now=start)
    with pytest.raises(ValueError, match="hết hạn"):
        store.confirm_selection(old["request_id"], page(A), page(A), now=start + timedelta(minutes=3))
    newer = store.create_selection_request("candidate:one")
    with pytest.raises(ValueError, match="không khớp"):
        store.confirm_selection(old["request_id"], page(A), page(A), now=start)
    reply = handle(json.dumps({"type":"selection_confirm", "schema":1,
        "request_id":newer["request_id"], "page_url":page(A), "page_url_after":page(A),
        "observed_name":""}).encode(), store.path.parent.parent / "settings.json")
    assert reply["channel_id"] == A and reply["id_source"] == "studio_url_user_confirmed"
    stale_chrome = {**candidate, "id":A, "id_source":"user_supplied"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[stale_chrome]})
    saved = StudioConnectorStore(store.path.parent.parent / "settings.json").load()["channels"][0]
    assert saved["id_source"] == "studio_url_user_confirmed"
    assert saved["identity_verified_at"]


def test_selection_request_rejects_changed_record_and_verified_wrong_id(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[catalog_record(A, "Verified")]})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = store.create_selection_request(f"id:{A}", now=start)
    with pytest.raises(ValueError, match="khác ID"):
        store.confirm_selection(request["request_id"], page(B), page(B), now=start)
    confirmed = store.confirm_selection(request["request_id"], page(A), page(A), "Verified", now=start)
    assert confirmed["id"] == A and confirmed["id_source"] == "studio_url_user_confirmed"
    assert confirmed["identity_evidence_url"] == f"https://studio.youtube.com/channel/{A}"
    with pytest.raises(ValueError, match="chứng cứ Studio"):
        store.link_manual_id(f"id:{A}", B)
    candidate = {**catalog_record(B, "Candidate"), "key":"candidate:second", "id":"",
                 "id_source":"missing", "source_url":"https://studio.youtube.com/"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[catalog_record(A, "Verified"), candidate]})
    second_request = store.create_selection_request("candidate:second", now=start)
    store.link_manual_id("candidate:second", B)
    with pytest.raises(ValueError, match="đã thay đổi"):
        store.confirm_selection(second_request["request_id"], page(B), page(B), now=start)
    assert store.catalog_records()[0]["id"] == A


def test_stale_catalog_requires_fresh_explicit_url_confirmation(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[catalog_record(A, "Stale")]})
    future = datetime.now(timezone.utc) + timedelta(days=3)
    assert store.load(now=future)["channels"][0]["status"] == "unavailable"
    request = store.create_selection_request(f"id:{A}", now=future)
    assert store.catalog_records()[0]["status"] == "identity_verified"
    assert request["status"] == "pending"
    confirmed = store.confirm_selection(request["request_id"], page(A), page(A), now=future)
    assert confirmed["status"] == "identity_user_confirmed"
    assert store.load(now=future)["channels"][0]["status"] == "identity_user_confirmed"


def test_bulk_sync_missing_name_keeps_previous_name(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    store.save_catalog({"type":"catalog_sync", "schema":1,
                        "records":[catalog_record(A, "Known name")]})
    nameless = {**catalog_record(A, ""), "name_source":"missing"}
    store.save_catalog({"type":"catalog_sync", "schema":1, "records":[nameless]})
    record = store.load()["channels"][0]
    assert record["name"] == "Known name"
    assert record["status"] == "identity_verified"


def test_bulk_sync_rejects_forged_source_labels(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    forged = {**catalog_record(A, "A"), "id_source":"oauth_verified"}
    with pytest.raises(ValueError, match="catalog không hợp lệ"):
        store.save_catalog({"type":"catalog_sync", "schema":1, "records":[forged]})


def test_extension_scope_and_native_launcher_origin_guard():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "studio_connector_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["permissions"] == ["activeTab", "nativeMessaging", "sidePanel", "storage"]
    assert manifest["side_panel"]["default_path"] == "sidepanel.html"
    assert "default_popup" not in manifest["action"]
    assert manifest["content_scripts"][0]["matches"] == ["https://studio.youtube.com/*"]
    assert manifest["host_permissions"] == ["https://studio.youtube.com/*"]
    installer = (root / "scripts" / "install-studio-connector.ps1").read_text(encoding="utf-8")
    assert 'args[0] != "chrome-extension://$ExtensionId/"' in installer
    assert "allowed_origins" in installer
    assert "ReadExact(input, 4)" in installer
    assert "child.StandardInput.Close()" in installer
    assert "var frameHeader = ReadExact(input, 4)" in installer
    assert "var requestHeader = frameTask.Result[0]" in installer
    assert "var header = frameTask.Result[0]" not in installer


def test_popup_timeouts_progress_and_native_failures():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the extension popup test")
    script = Path(__file__).resolve().with_name("studio_connector_popup.test.cjs")
    subprocess.run(["node", str(script)], check=True, capture_output=True, text=True)


def test_sidepanel_catalog_bulk_scan_persistence_and_recovery():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the Side Panel test")
    script = Path(__file__).resolve().with_name("studio_connector_sidepanel.test.cjs")
    subprocess.run(["node", str(script)], check=True, capture_output=True, text=True)


def test_native_messaging_frame_and_connection_loss(tmp_path):
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path)
    payload = json.dumps(message()).encode()
    command = [sys.executable, "-m", "te.studio.studio_connector_host"]
    completed = subprocess.run(command, input=struct.pack("<I", len(payload)) + payload,
                               capture_output=True, env=env, check=True)
    def frames(output):
        result = []
        while output:
            size = struct.unpack("<I", output[:4])[0]
            result.append(json.loads(output[4:4 + size]))
            output = output[4 + size:]
        return result
    replies = frames(completed.stdout)
    assert replies[0] == {"stage": "saving"}
    assert replies[1]["ok"] is True and replies[1]["verified_count"] == 1
    assert (tmp_path / "TEStudio" / "studio-connector" / "scan.json").exists()
    assert "python completed ok" in (tmp_path / "TEStudio" / "studio-connector" / "connector.log").read_text()
    broken = subprocess.run(command, input=struct.pack("<I", len(payload)) + payload[:3],
                            capture_output=True, env=env, check=True)
    assert frames(broken.stdout)[0]["ok"] is False
    malformed = subprocess.run(command, input=struct.pack("<I", 3) + b"bad",
                               capture_output=True, env=env, check=True)
    assert frames(malformed.stdout)[0] == {"stage": "saving"}
    assert frames(malformed.stdout)[1]["ok"] is False
    assert "python saving JSONDecodeError" in (tmp_path / "TEStudio" / "studio-connector" / "connector.log").read_text()


def test_persists_each_selected_channel_and_marks_old_observations_unavailable(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    store.save_message(message(A))
    store.save_message(message(B))
    restarted = StudioConnectorStore(tmp_path / "settings.json")
    fresh = restarted.load()
    assert {item["id"] for item in fresh["channels"]} == {A, B}
    assert all(item["status"] == "identity_verified" for item in fresh["channels"])
    future = datetime.now(timezone.utc) + timedelta(days=2)
    stale = restarted.load(now=future)
    assert all(item["status"] == "unavailable" for item in stale["channels"])


def test_scan_b_does_not_erase_verified_a_or_downgrade_its_name(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    store.save_message(message(A, [{"id": A, "name": "Channel A", "source_url": page(A)}]))
    store.save_message(message(B, [{"id": B, "name": "Channel B", "source_url": page(B)}]))
    store.save_message(message(A, [{"id": A, "name": "", "source_url": page(A)}]))
    records = {item["id"]: item for item in StudioConnectorStore(tmp_path / "settings.json").load()["channels"]}
    assert set(records) == {A, B}
    assert records[A]["name"] == "Channel A" and records[A]["status"] == "identity_verified"
    assert records[B]["name"] == "Channel B" and records[B]["status"] == "identity_verified"


def test_repeated_manual_active_scans_preserve_channels_and_source(tmp_path):
    store = StudioConnectorStore(tmp_path / "settings.json")
    def manual(channel, name):
        return {**message(channel, [{"id": channel, "name": "", "source_url": page(channel)}]),
                "manual_name": name}
    store.save_message(manual(A, "大谷翔平速報"))
    store.save_message(manual(B, "Delegated B"))
    store.save_message(manual(A, "Updated A"))
    records = {item["id"]: item for item in StudioConnectorStore(tmp_path / "settings.json").load()["channels"]}
    assert set(records) == {A, B}
    assert records[A]["name"] == "Updated A" and records[A]["name_source"] == "user_supplied"
    assert records[B]["name"] == "Delegated B" and records[B]["status"] == "identity_user_named"
    store.save_message(message(A, [{"id": A, "name": "DOM A", "source_url": page(A)}]))
    records = {item["id"]: item for item in store.load()["channels"]}
    assert records[A]["name"] == "DOM A" and records[A]["name_source"] == "studio_identity_dom"


def test_project_destination_is_separate_from_api_authorization(tmp_path):
    from te.studio.service import StudioService
    repo = StudioService(tmp_path / "settings.json")
    repo.set_directories(tmp_path / "projects", tmp_path / "output")
    state = repo.create_project("Destination")
    state.publisher["channel_id"] = A
    state.publisher["connection_type"] = "studio_connector"
    repo.save_project(state)
    loaded = repo.open_project(state.directory)
    assert loaded.publisher["channel_id"] == A
    assert loaded.publisher["connection_type"] == "studio_connector"
    assert normalize_metadata({"channel_id": B})["connection_type"] == "youtube_api"
    publisher = PublisherStore(repo.config_path)
    with pytest.raises(ValueError, match="chưa hỗ trợ upload"):
        publisher.prepare("p", loaded.publisher, {"sha256": "x"})


def test_content_script_does_not_scan_unselected_studio_menu():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the mocked extension DOM fixture")
    extension = Path(__file__).resolve().parents[1] / "studio_connector_extension" / "content.js"
    fixture = r'''
const {studioId, scanStudioDocument} = require(process.argv[1]);
const A = "UC" + "a".repeat(22), B = "UC" + "b".repeat(22);
const entry = (name, id) => ({getClientRects:()=>[1], getAttribute:()=>null,
  querySelector:()=>null, closest:()=>({href:`https://studio.youtube.com/channel/${id}/videos`}), textContent:name});
const menu = {getClientRects:()=>[1], getAttribute:()=>null,
  querySelectorAll:()=>[entry("同じ名前", A),entry("同じ名前", B)]};
const doc = {querySelector:()=>({textContent:"同じ名前"}), querySelectorAll:()=>[menu]};
const result = scanStudioDocument(doc, `https://studio.youtube.com/channel/${A}/videos`);
if (result.channels.length !== 1 || result.channels[0].id !== A || result.diagnostics.switcher_entries !== 0 ||
    studioId(`https://evil.test/channel/${A}`) !== "") process.exit(1);
console.log(JSON.stringify(result));
'''
    completed = subprocess.run(["node", "-e", fixture, str(extension)], capture_output=True, text=True, check=True)
    scan = validate_scan(json.loads(completed.stdout))
    assert {item["id"] for item in scan["channels"]} == {A}


def test_content_script_sidebar_shadow_root_and_missing_name():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the mocked Studio DOM fixture")
    extension = Path(__file__).resolve().parents[1] / "studio_connector_extension" / "content.js"
    fixture = r'''
const {scanStudioDocument} = require(process.argv[1]);
const A = "UC" + "a".repeat(22), B = "UC" + "b".repeat(22);
const url = id => `https://studio.youtube.com/channel/${id}/videos`;
const nameNode = {textContent:"Sidebar A",getClientRects:()=>[1],getAttribute:()=>null};
const shadow = {querySelectorAll: selector => selector.includes("#channel-name") ? [nameNode] : []};
const sidebar = {querySelectorAll:()=>[],shadowRoot:shadow};
const entry = (name,id) => ({getClientRects:()=>[1],getAttribute:()=>null,querySelector:()=>null,
  closest:()=>({href:url(id)}),textContent:name});
const menu = {getClientRects:()=>[1],getAttribute:()=>null,
  querySelectorAll:()=>[entry("Sidebar A",A),entry("Delegated B",B)]};
const doc = (withSidebar,withMenu=true) => ({querySelectorAll: selector => selector.includes("ytcp-app-sidebar") ?
  (withSidebar ? [sidebar] : []) : selector.includes("ytcp-account-switcher") ? (withMenu ? [menu] : []) : []});
const missing = scanStudioDocument(doc(false,false),url(A));
if (!missing.diagnostics.active_id_found || missing.diagnostics.active_name_found ||
    missing.channels[0].id !== A || missing.channels[0].name !== "") process.exit(1);
const found = scanStudioDocument(doc(true,false),url(A));
if (found.channels[0].name !== "Sidebar A" || found.diagnostics.switcher_entries !== 0 ||
    found.diagnostics.switcher_detected || found.channels.length !== 1) process.exit(2);
console.log(JSON.stringify({missing,found}));
'''
    completed = subprocess.run(["node", "-e", fixture, str(extension)], capture_output=True, text=True, check=True)
    results = json.loads(completed.stdout)
    assert validate_scan(results["missing"])["channels"][0]["status"] == "identity_incomplete"
    assert validate_scan(results["found"])["channels"][0]["status"] == "identity_verified"


def test_identity_diagnostic_is_scoped_to_sidebar_components():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the diagnostic fixture")
    extension = Path(__file__).resolve().parents[1] / "studio_connector_extension" / "content.js"
    fixture = r'''
const {identityCandidates, activeChannelName} = require(process.argv[1]);
const A = "UC" + "a".repeat(22);
const channel = {tagName:"YT-FORMATTED-STRING",id:"channel-name",className:"channel-name",
  textContent:"大谷翔平速報",isContentEditable:false,getClientRects:()=>[1],
  getBoundingClientRect:()=>({top:90,left:40}),
  getAttribute:key=>key==="aria-label"?"owner@example.com":null};
const outside = {...channel,textContent:"Private video metadata"};
const shadow = {querySelectorAll:selector=>selector==="*"?[]:[channel]};
const sidebar = {shadowRoot:shadow,querySelectorAll:()=>[]};
const doc = {querySelectorAll:selector=>selector==="ytcp-app-sidebar"?[sidebar]:
  selector==="article"?[outside]:[]};
const result = identityCandidates(doc,`https://studio.youtube.com/channel/${A}/videos`);
if (activeChannelName(doc)!=="大谷翔平速報" || result.candidates.length!==1 ||
  result.candidates[0].text_sample!=="大谷翔平速報") process.exit(1);
console.log(JSON.stringify(result));
'''
    completed = subprocess.run(["node", "-e", fixture, str(extension)], capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    assert result["active_id_found"] is True
    assert result["candidates"][0]["id"] == "channel-name"
    assert "Private video" not in completed.stdout
    assert "owner@example.com" not in completed.stdout
