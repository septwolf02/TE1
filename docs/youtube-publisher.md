# TE Studio Phase 4A — YouTube Studio Publisher

Phase 4A supports an explicitly confirmed **private test upload** through a dedicated, headed Chromium profile. It does not publish publicly or schedule a video. The Publisher saves planned settings for later phases but currently applies only Japanese title, description, audience, and Private visibility. Thumbnail, tags, playlist, monetization, advertisements, and end screen are **not applied** in Phase 4A.

## Windows installation

From the TE repository in PowerShell:

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e '.[gui,publisher]'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m te.studio
```

Playwright runs a separate Chromium profile under `%LOCALAPPDATA%\TEStudio\youtube-publisher\browser`. Google authentication is performed manually on Google's own site in that browser. TE Studio never requests or saves a Google password. Google may block an automated browser sign-in; in that case Phase 4A cannot proceed and no bypass is attempted. The current account email is not automatically extracted, so check the account in Studio before confirming an upload.

## Safe local test with an Editor account

1. Create or open a project with a completed Full HD MP4. Open **7. Xuất bản YouTube**.
2. Choose **Kết nối Google**, sign in manually in the separate Chromium window, select your test channel in YouTube Studio, and close that window. Do not use a production channel for the first test.
3. Copy the real `UC…` channel ID from the `studio.youtube.com/channel/UC…` URL and enter its name for display. Choose **Làm mới danh sách kênh**. The app reopens that exact Studio channel and verifies that the Upload dialog and file input can be opened. This explicit channel flow is used because Studio Editor channels are not reliably listed by the public Data API. The display name is entered by the user; the ID and upload control are checked in Studio.
4. Set the MP4, Japanese title and description, audience, and `immediate` mode. Choose **Lưu thông tin xuất bản**. Check the selected Google account and channel ID in Studio.
5. Choose **Tải thử Riêng tư** and confirm. TE Studio checks the selected channel again, sets Private visibility, saves the upload, and records the returned video ID and URL only after the upload dialog closes. Confirm the video is Private in YouTube Studio before doing anything else.
6. Close and reopen the project to verify the saved metadata and job status. If the browser or app fails after upload begins, inspect Studio for an existing video. TE Studio marks the attempt `needs_attention` and will not blindly upload the same file again.

The profile and persistent job journal are machine-local under `%LOCALAPPDATA%\TEStudio\youtube-publisher`; project publishing metadata is in `studio.json`. **Ngắt kết nối** removes the dedicated profile and the local verified-channel list while keeping old jobs. No authentication data, MP4, or thumbnail is added to the Git repository.

This browser workflow has automated mocked tests. Live Google authentication, Editor channel verification and actual upload must be checked on Windows with a test channel. YouTube Studio's UI can change; an unrecognized control stops the job and leaves it for manual review.
