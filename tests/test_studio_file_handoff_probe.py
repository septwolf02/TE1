"""File-input probe tests; the browser integration runs only when explicitly enabled."""
import importlib.util
import os
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe_studio_file_handoff.py"
spec = importlib.util.spec_from_file_location("studio_file_handoff_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_validates_selected_mp4_and_rejects_other_files(tmp_path):
    selected = tmp_path / "日本語 test.mp4"
    selected.write_bytes(b"\0\0\0\x18ftypisom")
    assert probe.validate_mp4(selected) == (selected.resolve(), 12,
                                             list(b"\0\0\0\x18ftypisom"))
    selected.write_bytes(b"not an mp4 file")
    with pytest.raises(ValueError, match="ftyp"):
        probe.validate_mp4(selected)
    with pytest.raises(FileNotFoundError):
        probe.validate_mp4(tmp_path / "missing.mp4")
    other = tmp_path / "private.txt"
    other.write_bytes(b"\0\0\0\x18ftypisom")
    with pytest.raises(ValueError, match=".mp4"):
        probe.validate_mp4(other)


def test_loopback_page_never_serves_local_files(tmp_path):
    from http.server import ThreadingHTTPServer

    private = tmp_path / "private.txt"
    private.write_text("secret", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), probe.ProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            page = response.read()
        assert b'id="probe-video"' in page and b"secret" not in page
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.server_port}/private.txt")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not os.environ.get("TE_FILE_HANDOFF_BROWSER"),
                    reason="requires an explicitly selected Chrome/Chromium executable and Playwright")
def test_cdp_handoff_of_large_sparse_mp4_to_real_browser(tmp_path):
    pytest.importorskip("playwright.sync_api")
    selected = tmp_path / "large-test.mp4"
    with selected.open("wb") as stream:
        stream.write(b"\0\0\0\x18ftypisom")
        stream.truncate(1024**3)
    result = probe.run_probe(selected, executable=os.environ["TE_FILE_HANDOFF_BROWSER"], headless=True)
    assert result["received"] is True
    assert result["filename"] == selected.name
    assert result["bytes"] == 1024**3
    assert result["header_match"] is True
