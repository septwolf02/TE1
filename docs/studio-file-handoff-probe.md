# Phase 4B.2A — Local MP4 to Chrome file-input probe

This is an isolated feasibility test. It does not connect to YouTube, publish a video, change TE Publisher jobs, or change the installed Studio Connector. The separate MV3 extension requests the powerful Chrome `debugger` permission **only for this test**. Do not install it as a publishing feature.

## Options considered

| Approach | Can populate a file input? | Large file and restart behavior | Security and interaction |
| --- | --- | --- | --- |
| Native Messaging chunks → `Blob`/`File`/`DataTransfer` | Likely for ordinary inputs, but not demonstrated here | Native messages are bounded; reconstructing a multi-GB `File` risks large renderer/extension memory use, and a restart loses the transfer | Must bound message sizes, authorize every chunk, and prevent page access. Not selected. |
| Loopback file service → browser `fetch` | Fetch alone cannot set `input.files` to a filesystem path; reconstructing a `File` has the same large-file concern | A streamed download could stay bounded, but the resulting browser-side file-input handoff still needs proof | Requires a local server, per-task token, origin checks, path allowlist, lifetime and error handling. Not selected. |
| Windows native file-dialog automation | The OS dialog can select a path without copying MP4 bytes into JS | Low memory, but fragile across focus, display scaling, language, Chrome updates and restarts | Usually needs an interactive desktop. Difficult to run unattended. Not selected. |
| Chromium CDP `DOM.setFileInputFiles` via `chrome.debugger` | **Direct CDP proven on a local test input in Cloud Chromium.** Separate MV3 `chrome.debugger` adapter is provided for Windows testing. | CDP receives a path, not video bytes. Reattach and revalidate the task after a restart. A 1 GiB sparse MP4 was accepted; process memory was not formally profiled. | Requires Chrome's broad `debugger` permission and may show a debugging banner or be blocked by policy. Production must obtain only an allowlisted, freshly verified TE path through Native Messaging. |

The direct CDP probe used `DOM.getDocument` → `DOM.querySelector` → `DOM.setFileInputFiles`. The local page's change handler received the selected filename, exact 1 GiB size, and the first 12 MP4 header bytes. No MP4 bytes were sent through Native Messaging, HTTP or a JavaScript message. The loopback server serves only fixed HTML and returns 404 for other paths. The test page can read the explicitly selected `File`, as any upload form can; it has no API to request other local paths.

The MV3 adapter in `studio_file_handoff_probe/` accepts a path and expected byte count typed into its own popup. It rejects tabs outside `http://127.0.0.1:<port>/`, checks the expected filename and size, and detaches the debugger in a `finally` block. It has **no YouTube host permission** and no Native Messaging access. Its path field is suitable only for a user-driven experiment; production must replace it with a prepared-job allowlist and one-time authorization from TE.

## Windows test with installed Chrome

Use a small disposable MP4 first. Do not use an unpublished production video. From PowerShell:

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install playwright
$mp4 = 'D:\TE-Test\small-test.mp4'
(Get-Item $mp4).Length
.\.venv\Scripts\python.exe scripts\probe_studio_file_handoff.py $mp4
```

The CLI opens installed Google Chrome against a fixed local HTML page, sends the path through CDP, checks the filename, byte count and first 12 bytes, then closes the test browser. It does **not** require `playwright install chromium`. Success prints JSON with `received: true` and `header_match: true`. This tests CDP in Chrome, not the extension API.

To test the separate MV3 adapter against your ordinary Chrome installation:

1. In one PowerShell window run `.\.venv\Scripts\python.exe scripts\probe_studio_file_handoff.py --serve`. Keep it open and copy the printed `http://127.0.0.1:<port>/` URL.
2. At `chrome://extensions`, enable Developer mode and **Load unpacked** from `D:\TE-Git\studio_file_handoff_probe`. Chrome will warn about `debugger` permission. This is a separate test extension; the installed TE Studio Connector is unchanged.
3. Open the printed URL in the same Chrome window. Click the test extension's icon. Paste the absolute MP4 path and byte count from `(Get-Item $mp4).Length`, then click **Đưa MP4 vào file input thử nghiệm**.
4. Confirm both the extension popup and local page report the same filename and byte count. Try a wrong size and a non-localhost tab; both must fail. Close the test server and remove the test extension afterward.

No Native Host reinstall or TE Connector reload is needed for this prototype. The MP4 never leaves the local test browser/page. The CLI result in Cloud does **not** prove the MV3 `chrome.debugger` call works on Windows Chrome; that requires the manual test above.

## Remaining Studio integration work

The real YouTube Studio upload input, its possible iframe/shadow-root location, and its behavior after a CDP file selection have not been inspected here. A future production implementation must: confirm the active Channel ID immediately before file selection, obtain only the exact prepared MP4 path from a Native Host allowlist, use a one-time request bound to that channel/job/tab, validate the input selector on the real Studio DOM, and record ambiguous outcomes without retrying. Chrome's `debugger` permission and warning must be acceptable to the user. No upload automation or unattended claim follows from this probe.
