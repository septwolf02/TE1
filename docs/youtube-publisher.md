# TE Studio Phase 4A — YouTube Studio Publisher

## Current capability

The Publisher saves project metadata in `studio.json` and keeps existing publishing jobs and channel IDs in the machine-local job journal. **Automated upload is temporarily disabled.** The previous Playwright-controlled Chrome sign-in was rejected by Google with “This browser or app may not be secure.” [Playwright's browser API](https://playwright.dev/python/docs/api/class-browsertype) documents that `chromium_sandbox` defaults to `false`, which explains the `--no-sandbox` warning observed in Chrome. TE Studio no longer launches a Playwright browser, so it no longer adds that flag. TE Studio now opens YouTube Studio in the ordinary system browser for manual sign-in and upload. Opening a Studio page does **not** prove the Google identity, channel authorization, or publishing capability to TE Studio. Previously saved channel entries are displayed as unverified.

Google [requires secure browsers for OAuth](https://developers.google.com/identity/protocols/oauth2/policies) and its [desktop OAuth flow](https://developers.google.com/identity/protocols/oauth2/native-app) uses the system browser with a loopback redirect. That OAuth flow would verify an account's identity, but does not grant an Editor's Studio channel permission to YouTube APIs. [YouTube's channel permissions guidance](https://support.google.com/youtubecreatorstudio/answer/9481328) explicitly lists YouTube APIs among features unavailable to invited users, while Editors can upload in YouTube Studio itself. Identity, channel permission, and publishing capability must therefore be treated as separate checks.

No OAuth client is configured in TE Studio. The **Mở YouTube Studio** button opens the regular browser for manual use; it is not an OAuth connection and does not share the browser session with TE Studio. TE Studio does not collect Google passwords, copy cookies or tokens, control a personal Chrome profile, or use stealth or security-disable flags. The former dedicated Playwright profile remains outside the repository under `%LOCALAPPDATA%\TEStudio\youtube-publisher\browser` until the user explicitly chooses **Xóa hồ sơ Playwright cũ**. Existing project metadata and job records are retained.

## Windows update

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
.\.venv\Scripts\python.exe -m te.studio
```

No Playwright browser installation is needed for this Publisher path. To upload now, open the Publisher, save the intended MP4 and metadata, then use **Mở YouTube Studio**. Sign in and select the intended Editor-accessible channel in the browser. Verify the account and channel there, upload the MP4 manually, and set visibility and other settings in Studio. TE Studio does not claim this manual action completed or update a job to `published` automatically. Any prior uncertain job remains available for review, and the duplicate-prevention records are preserved.
