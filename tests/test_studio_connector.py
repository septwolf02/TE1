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


def test_extension_scope_and_native_launcher_origin_guard():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "studio_connector_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["permissions"] == ["activeTab", "nativeMessaging"]
    assert manifest["content_scripts"][0]["matches"] == ["https://studio.youtube.com/*"]
    assert "host_permissions" not in manifest
    installer = (root / "scripts" / "install-studio-connector.ps1").read_text(encoding="utf-8")
    assert 'args[0] != "chrome-extension://$ExtensionId/"' in installer
    assert "allowed_origins" in installer
    assert "ReadExact(input, 4)" in installer
    assert "child.StandardInput.Close()" in installer


def test_popup_timeouts_progress_and_native_failures():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable for the extension popup test")
    script = Path(__file__).resolve().with_name("studio_connector_popup.test.cjs")
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


def test_content_script_parses_mocked_visible_studio_dom():
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
if (result.channels.length !== 3 || result.channels[2].id !== B ||
    studioId(`https://evil.test/channel/${A}`) !== "") process.exit(1);
console.log(JSON.stringify(result));
'''
    completed = subprocess.run(["node", "-e", fixture, str(extension)], capture_output=True, text=True, check=True)
    scan = validate_scan(json.loads(completed.stdout))
    assert {item["id"] for item in scan["channels"]} == {A, B}
