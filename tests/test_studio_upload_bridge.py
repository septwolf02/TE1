"""Local Connector upload handoff tests; no YouTube request is made."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from te.studio.publisher import PublisherStore
from te.studio.studio_connector import StudioConnectorStore
from te.studio.studio_connector_host import handle
from te.studio.studio_upload_bridge import StudioUploadBridge

A = "UC" + "a" * 22
B = "UC" + "b" * 22


def setup_job(tmp_path):
    tmp_path.mkdir(parents=True,exist_ok=True)
    config = tmp_path / "settings.json"
    catalog = StudioConnectorStore(config)
    record = {"key":"candidate:one", "id":"", "handle":"@test", "discovered_name":"日本語",
              "display_name":"日本語", "name_source":"studio_switcher_dom",
              "handle_source":"studio_switcher_dom", "id_source":"missing", "status":"discovered",
              "permission_status":"permission_unverified", "upload_status":"upload_unverified",
              "source_url":"https://studio.youtube.com/", "evidence":"visible_switcher_item",
              "user_edited":False}
    catalog.save_catalog({"type":"catalog_sync", "schema":1, "records":[record]})
    identity = catalog.create_selection_request(record["key"])
    url = f"https://studio.youtube.com/channel/{A}"
    catalog.confirm_selection(identity["request_id"],url,url,"日本語")
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"\0\0\0\x18ftypisomvideo")
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    metadata = {"connection_type":"studio_connector", "studio_catalog_key":record["key"],
                "channel_id":A, "video_path":str(video), "thumbnail_path":str(thumb),
                "title":"日本語の題名", "audience":"not_made_for_kids"}
    job = PublisherStore(config).prepare_studio_private("project",metadata)
    return config, video, job


def studio_url(channel=A):
    return f"https://studio.youtube.com/channel/{channel}/videos/upload"


def test_host_handoff_checks_channel_and_is_one_shot(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    fetched = handle(b'{"type":"upload_get","schema":1}',config)["request"]
    assert fetched["pending"] and fetched["request_id"] == request["request_id"]
    assert "path" not in json.dumps(fetched)
    with pytest.raises(ValueError,match="Channel ID"):
        bridge.claim(request["request_id"],studio_url(B),studio_url(B))
    with pytest.raises(ValueError,match="Channel ID"):
        bridge.claim(request["request_id"],studio_url(),studio_url(B))
    claimed = handle(json.dumps({"type":"upload_claim","schema":1,
        "request_id":request["request_id"],"page_url":studio_url(),
        "page_url_after":studio_url()}).encode(),config)["file"]
    assert claimed["path"] == str(video.resolve())
    assert claimed["bytes"] == video.stat().st_size
    assert claimed["filename"] == video.name and claimed["channel_id"] == A
    with pytest.raises(ValueError,match="đã dùng"):
        bridge.claim(request["request_id"],studio_url(),studio_url())
    assert not bridge.pending()["pending"]
    handle(json.dumps({"type":"upload_started","schema":1,
                       "request_id":request["request_id"]}).encode(),config)
    handle(json.dumps({"type":"upload_selected","schema":1,
                       "request_id":request["request_id"],"filename":video.name,
                       "bytes":video.stat().st_size}).encode(),config)
    stored = PublisherStore(config,recover_studio=False).latest("project")
    assert stored["status"] == "uploading"
    assert stored["last_confirmed_operation"] == "browser_file_input_received"
    assert stored["video_id"] == "" and stored["visibility"] == "private"
    with pytest.raises(ValueError,match="sẵn sàng"):
        PublisherStore(config,recover_studio=False).begin_attempt(job["id"])


def test_changed_thumbnail_is_blocked_before_file_selection(tmp_path):
    config, _, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    (tmp_path / "thumb.png").write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    with pytest.raises(ValueError, match="Thumbnail đã thay đổi"):
        bridge.claim(request["request_id"], studio_url(), studio_url())
    assert PublisherStore(config, recover_studio=False).latest("project")["status"] == "waiting_for_browser"


def test_missing_or_changed_mp4_and_expired_request_never_claim(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    video.unlink()
    with pytest.raises(FileNotFoundError):
        bridge.claim(request["request_id"],studio_url(),studio_url())
    assert PublisherStore(config,recover_studio=False).latest("project")["status"] == "waiting_for_browser"
    assert not bridge.pending(now=datetime.now(timezone.utc)+timedelta(minutes=6))["pending"]
    assert PublisherStore(config,recover_studio=False).latest("project")["status"] == "failed_before_upload"


def test_duplicate_request_disconnect_and_restart_are_conservative(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    with pytest.raises(ValueError,match="chờ"):
        bridge.queue(job["id"])
    bridge.claim(request["request_id"],studio_url(),studio_url())
    bridge.fail(request["request_id"],"debugger_failure")
    assert PublisherStore(config,recover_studio=False).latest("project")["status"] == "failed_before_upload"
    config2, video2, job2 = setup_job(tmp_path / "other")
    bridge2 = StudioUploadBridge(config2)
    pending = bridge2.queue(job2["id"])
    bridge2.claim(pending["request_id"],studio_url(),studio_url())
    bridge2.started(pending["request_id"])
    bridge2.fail(pending["request_id"],"chrome_disconnected")
    assert PublisherStore(config2,recover_studio=False).latest("project")["status"] == "needs_attention"
    with pytest.raises(ValueError,match="PRIVATE"):
        bridge2.queue(job2["id"])
    config3, _, job3 = setup_job(tmp_path / "restart")
    StudioUploadBridge(config3).queue(job3["id"])
    assert PublisherStore(config3).latest("project")["status"] == "needs_attention"
    assert PublisherStore(config3,recover_studio=False).latest("project")["video_id"] == ""


def test_wrong_file_receipt_marks_uncertain_without_video_id(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    bridge.claim(request["request_id"],studio_url(),studio_url())
    bridge.started(request["request_id"])
    with pytest.raises(ValueError,match="sai tên"):
        bridge.file_selected(request["request_id"],"other.mp4",video.stat().st_size)
    stored = PublisherStore(config,recover_studio=False).latest("project")
    assert stored["status"] == "needs_attention" and stored["video_id"] == ""


def test_studio_ui_does_not_react_after_file_selection_is_uncertain(tmp_path):
    config, _, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    bridge.claim(request["request_id"], studio_url(), studio_url())
    bridge.started(request["request_id"])
    handle(json.dumps({"type":"upload_failed","schema":1,"request_id":request["request_id"],
        "reason":"studio_no_reaction","diagnostics":{"browser_step":"waiting_for_studio_reaction",
        "dialog_open":True,"candidate_count":1,"rejected":["studio_reaction_unconfirmed"],
        "cdp_attached":True}}).encode(), config)
    stored = PublisherStore(config, recover_studio=False).latest("project")
    assert stored["status"] == "needs_attention" and "chưa hiển thị" in stored["error"]
    assert bridge.pending() == {"pending":False}
    with pytest.raises(ValueError, match="không tạo lại"):
        PublisherStore(config, recover_studio=False).prepare_studio_private("project", job["metadata"])


def test_pre_upload_failure_can_retry_same_mp4_with_new_job_and_attempt(tmp_path):
    config, video, first = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(first["id"])
    bridge.claim(request["request_id"], studio_url(), studio_url())
    bridge.fail(request["request_id"], "missing_input", {"browser_step":"waiting_for_upload_dialog",
        "dialog_open":False,"candidate_count":0,"rejected":[],"cdp_attached":True})
    store = PublisherStore(config, recover_studio=False)
    previous = next(job for job in store.data["jobs"] if job["id"] == first["id"])
    assert previous["status"] == "failed_before_upload" and "YouTube Studio" in previous["error"]
    state_payload = json.dumps({"type":"upload_state","schema":1,
                                "request_id":request["request_id"]}).encode()
    assert handle(state_payload, config)["safe_to_clear"] is True
    new = store.prepare_studio_private("project", first["metadata"])
    assert new["id"] != first["id"] and new["fingerprint"] == first["fingerprint"]
    second = bridge.queue(new["id"])
    assert second["attempt_id"] != request["attempt_id"]
    assert handle(state_payload, config)["safe_to_clear"] is True
    assert next(job for job in PublisherStore(config, recover_studio=False).data["jobs"]
                if job["id"] == first["id"])["error"] == previous["error"]


def test_uncertain_or_unproven_pre_upload_failure_cannot_retry(tmp_path):
    config, _, first = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(first["id"])
    bridge.claim(request["request_id"], studio_url(), studio_url())
    bridge.started(request["request_id"])
    bridge.fail(request["request_id"], "chrome_disconnected")
    assert StudioUploadBridge(config).request_state(request["request_id"])["safe_to_clear"] is False
    with pytest.raises(ValueError, match="không tạo lại"):
        PublisherStore(config, recover_studio=False).prepare_studio_private("project", first["metadata"])
    config2, _, other = setup_job(tmp_path / "unproven")
    PublisherStore(config2, recover_studio=False).update(other["id"], status="failed_before_upload")
    with pytest.raises(ValueError, match="chưa được xác nhận"):
        PublisherStore(config2, recover_studio=False).prepare_studio_private("project", other["metadata"])


def test_host_rejects_malformed_upload_diagnostics(tmp_path):
    config, _, job = setup_job(tmp_path)
    request = StudioUploadBridge(config).queue(job["id"])
    payload = {"type":"upload_failed","schema":1,"request_id":request["request_id"],
               "reason":"missing_input","diagnostics":{"browser_step":"x","secret":"never"}}
    with pytest.raises(ValueError, match="Chẩn đoán"):
        handle(json.dumps(payload).encode(), config)


def test_private_completion_requires_verified_video_and_preserves_duplicate_lock(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    bridge.claim(request["request_id"], studio_url(), studio_url())
    bridge.started(request["request_id"])
    bridge.file_selected(request["request_id"], video.name, video.stat().st_size)
    task = handle(json.dumps({"type":"upload_resume", "schema":1,
        "request_id":request["request_id"]}).encode(), config)["task"]
    assert task["title"] == "日本語の題名"
    assert task["thumbnail_name"] == "thumb.png" and task["audience"] == "not_made_for_kids"
    with pytest.raises(ValueError, match="chứng cứ"):
        bridge.complete(request["request_id"], studio_url(), "v" * 11, "private")
    for step in ("entering_details", "thumbnail_selected", "audience_selected",
                 "setting_private", "saving_private", "verifying_video"):
        handle(json.dumps({"type":"upload_progress", "schema":1,
            "request_id":request["request_id"], "step":step}).encode(), config)
    for page, video_id, visibility in ((studio_url(B), "v" * 11, "private"),
                                       (studio_url(), "bad", "private"),
                                       (studio_url(), "v" * 11, "public")):
        with pytest.raises(ValueError, match="chứng cứ"):
            bridge.complete(request["request_id"], page, video_id, visibility)
    result = handle(json.dumps({"type":"upload_complete", "schema":1,
        "request_id":request["request_id"], "page_url":studio_url(),
        "video_id":"v" * 11, "visibility":"private"}).encode(), config)
    assert result["video_url"] == "https://www.youtube.com/watch?v=" + "v" * 11
    assert PublisherStore(config, recover_studio=False).latest("project")["status"] == "completed_private"
    assert bridge.request_state(request["request_id"])["safe_to_clear"]
    with pytest.raises(ValueError, match="không tạo lại"):
        PublisherStore(config, recover_studio=False).prepare_studio_private("project", job["metadata"])


def test_uncertain_failure_after_save_never_allows_automatic_retry(tmp_path):
    config, video, job = setup_job(tmp_path)
    bridge = StudioUploadBridge(config)
    request = bridge.queue(job["id"])
    bridge.claim(request["request_id"], studio_url(), studio_url())
    bridge.started(request["request_id"])
    bridge.file_selected(request["request_id"], video.name, video.stat().st_size)
    bridge.progress(request["request_id"], "saving_private")
    assert bridge.resume(request["request_id"])["stage"] == "saving"
    with pytest.raises(ValueError, match="chờ"):
        bridge.queue(job["id"])
    with pytest.raises(ValueError, match="cấu hình lại"):
        bridge.progress(request["request_id"], "entering_details")
    bridge.fail(request["request_id"], "private_unverified", {"browser_step":"verifying_video"})
    stored = PublisherStore(config, recover_studio=False).latest("project")
    assert stored["status"] == "needs_attention" and stored["video_id"] == ""
    assert "verifying_video" in stored["error"]
    with pytest.raises(ValueError, match="không tạo lại"):
        PublisherStore(config, recover_studio=False).prepare_studio_private("project", job["metadata"])
    candidate = handle(b'{"type":"upload_reconcile_get","schema":1}', config)["request"]
    assert candidate["pending"] and candidate["title"] == "日本語の題名"
    payload = {"type":"upload_reconcile", "schema":1, "request_id":request["request_id"],
               "page_url":studio_url(), "video_id":"v" * 11, "visibility":"private"}
    with pytest.raises(ValueError, match="chứng cứ"):
        handle(json.dumps(dict(payload, page_url=studio_url(B))).encode(), config)
    with pytest.raises(ValueError, match="chứng cứ"):
        handle(json.dumps(dict(payload, visibility="public")).encode(), config)
    result = handle(json.dumps(payload).encode(), config)
    assert result["video_id"] == "v" * 11
    assert PublisherStore(config, recover_studio=False).latest("project")["status"] == "completed_private"
    assert bridge.reconcile_candidate() == {"pending":False}
