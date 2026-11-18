"""Isolated CDP file-input probe. Never opens YouTube or uploads a file.

Requires the optional Python Playwright package and an installed Chrome/Chromium.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread


TEST_PAGE = b"""<!doctype html><meta charset="utf-8"><title>TE file handoff probe</title>
<label>Local test MP4 <input id="probe-video" type="file" accept="video/mp4"></label>
<pre id="receipt">Waiting for file</pre>
<script>
document.querySelector('#probe-video').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  const head = [...new Uint8Array(await file.slice(0, 12).arrayBuffer())];
  window.probeReceipt = {name:file.name, size:file.size, head};
  document.querySelector('#receipt').textContent = JSON.stringify(window.probeReceipt);
});
</script>"""


def validate_mp4(path: Path) -> tuple[Path, int, list[int]]:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("Select an existing .mp4 test file.")
    size = path.stat().st_size
    if not 12 <= size <= 256 * 1024**3:
        raise ValueError("Test MP4 size must be between 12 bytes and 256 GiB.")
    with path.open("rb") as stream:
        head = list(stream.read(12))
    if bytes(head[4:8]) != b"ftyp":
        raise ValueError("Selected file has no MP4 ftyp header.")
    return path, size, head


class ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(TEST_PAGE)))
        self.end_headers()
        self.wfile.write(TEST_PAGE)

    def log_message(self, _format, *_args):
        pass


def run_probe(mp4: Path, *, executable: str | None = None, headless: bool = False) -> dict:
    """Give a local test input the path via CDP; only read a 12-byte browser slice."""
    path, size, head = validate_mp4(mp4)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Python Playwright first: python -m pip install playwright") from exc

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            options = {"executable_path": executable} if executable else {"channel": "chrome"}
            browser = playwright.chromium.launch(headless=headless, **options)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.continue_() if
                           route.request.url.startswith(f"http://127.0.0.1:{server.server_port}/")
                           else route.abort())
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
                cdp = page.context.new_cdp_session(page)
                root = cdp.send("DOM.getDocument")
                node = cdp.send("DOM.querySelector", {"nodeId": root["root"]["nodeId"],
                                                      "selector": "#probe-video"})
                if not node.get("nodeId"):
                    raise RuntimeError("Local test file input was not found.")
                cdp.send("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(path)]})
                page.wait_for_function("window.probeReceipt !== undefined", timeout=10000)
                receipt = page.evaluate("window.probeReceipt")
                if (receipt != {"name": path.name, "size": size, "head": head}):
                    raise RuntimeError("Browser file receipt does not match the selected MP4.")
                return {"received": True, "filename": receipt["name"], "bytes": receipt["size"],
                        "header_match": True, "browser": "installed Chrome" if not executable else executable}
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mp4", type=Path, nargs="?", help="Explicitly selected local test MP4")
    parser.add_argument("--executable", help="Use a specific Chromium executable instead of installed Chrome")
    parser.add_argument("--headless", action="store_true", help="Headless automation for local tests")
    parser.add_argument("--serve", action="store_true", help="Serve only the local test page for the MV3 probe")
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if args.mp4 is None:
        parser.error("mp4 is required unless --serve is used")
    print(json.dumps(run_probe(args.mp4, executable=args.executable, headless=args.headless), ensure_ascii=False))


if __name__ == "__main__":
    main()
