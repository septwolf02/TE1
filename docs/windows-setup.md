# Windows setup

1. Install Python 3.12 from python.org for your user account; administrator access is unnecessary.
2. Open PowerShell in the repository directory.
3. Create a virtual environment: `py -3.12 -m venv .venv`.
4. Install the package and test tools: `.venv\Scripts\python.exe -m pip install -e ".[test]"`.
5. Run tests: `.venv\Scripts\python.exe -m pytest`.

Create project directories outside this repository, such as `%USERPROFILE%\Videos\TE`. Register local images/videos by absolute path. The original files stay in place and are not modified.

For Module 6 rendering, install FFmpeg and ffprobe for your user account and add their directory to your user `PATH`. Confirm both with `ffmpeg -version` and `ffprobe -version` in a new PowerShell window. TE looks for Japanese fonts in the standard Windows user and system font folders (including Noto Sans CJK JP, Yu Gothic and Meiryo). An explicit `--font-path` or `TE_JAPANESE_FONT_PATH` takes precedence. Rendering does not need administrator privileges. Final MP4 files and previews are written under the project’s `generated/output/`, with temporary clips and logs under `generated/cache/render/`.

## Run the TE V1 local demo

Open PowerShell in the TE repository and run these commands:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -m te.demo
```

The last command creates a new project under `demo\test\`, renders `generated\output\preview.mp4` at 960×540, and prints its full path. To also render `generated\output\final.mp4` at 1920×1080, run:

```powershell
.\.venv\Scripts\python.exe -m te.demo --full
```

Each invocation creates a fresh timestamped project; it does not use personal media. `demo\test\` is ignored by Git. Run `.\.venv\Scripts\python.exe -m pytest -q` to check the code separately.

If TE cannot find a Japanese font, install Noto Sans CJK JP and pass its actual font file path, for example:

```powershell
.\.venv\Scripts\python.exe -m te.demo --full --font-family "Noto Sans CJK JP" --font-path "$env:LOCALAPPDATA\Microsoft\Windows\Fonts\NotoSansCJKjp-Regular.otf"
```

You can also set `$env:TE_JAPANESE_FONT_PATH` to that file path for both the demo and integration tests. TE checks that the file exists before rendering. UTF-8 is used for project JSON, subtitles, logs and FFmpeg text output; `PYTHONUTF8=1` is not required.

## VOICEVOX TTS on Windows

Start your existing VOICEVOX Engine, then run these PowerShell commands from the repository. They use the local HTTP API; TE does not install or alter VOICEVOX. The default endpoint is `127.0.0.1:50021` and can be changed with `--host` and `--port`.

```powershell
.\.venv\Scripts\python.exe -m te.tts check
.\.venv\Scripts\python.exe -m te.tts list
.\.venv\Scripts\python.exe -m te.tts sample --voice "青山龍星" --output ".\voicevox-samples\aoyama.wav"
.\.venv\Scripts\python.exe -m te.tts sample --voice "剣崎雌雄" --output ".\voicevox-samples\kenzaki.wav"
.\.venv\Scripts\python.exe -m te.tts sample --voice "後鬼" --output ".\voicevox-samples\goki.wav"
```

`list` prints the installed speaker/style names and their current engine IDs. Select a style by name with `--style "ノーマル"` or another style shown by `list`. The default speaker is 青山龍星; the other two are listed first when installed. The sample command accepts `--text`, `--speed`, `--pitch`, `--intonation`, `--volume`, and `--sample-rate`. Example:

```powershell
.\.venv\Scripts\python.exe -m te.tts sample --voice "青山龍星" --style "ノーマル" --text "大谷選手が、決勝の舞台に立ちました。" --speed 1.1 --pitch 0.02 --intonation 1.2 --volume 1.0 --output ".\voicevox-samples\sports.wav"
$env:TE_VOICEVOX_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_voicevox.py -q
Remove-Item Env:\TE_VOICEVOX_INTEGRATION
```

The normal suite mocks VOICEVOX and works without a running engine. The opt-in integration test above synthesizes a short WAV against the local engine. Sample WAV files under `voicevox-samples/` are ignored by Git.

To use VOICEVOX in a project, set `project.tts_settings` to `TTSSettings(provider="voicevox", voice_id="青山龍星", style="ノーマル", sample_rate=24000)` and save with `ProjectManager.save(...)`. `TTSService.generate_all(...)` then creates segment-addressed WAV files under the project's `generated/audio/`. The provider resolves speaker and style IDs from `/speakers` at generation time. Each audio record stores the selected speaker UUID, style ID, credit name, and that speaker's policy text and local `/speaker_info` reference. Review each speaker's current terms before publishing. Channel presets can be saved with `TTSService.save_voice_preset(project, project_dir, "channel-name", settings)` and applied with `apply_voice_preset(project, project_dir, "channel-name")`; a segment can have its own preset by passing its segment ID as the final argument. Changing text, provider, voice, style, or synthesis settings marks audio stale until regenerated.

## TTS-2 sentence-level VOICEVOX smoke test

With VOICEVOX Engine running, use these commands in PowerShell from the repository:

```powershell
git switch te-v1-render-test
git pull origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
$env:TE_VOICEVOX_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_sentence_speech.py tests\test_voicevox.py -q
Remove-Item Env:\TE_VOICEVOX_INTEGRATION
.\.venv\Scripts\python.exe -m te.tts.smoke --voice "青山龍星" --pause-ms 120
```

The smoke command creates a new project under `demo\test\`, prints each sentence ID and measured local/absolute timing, builds a timeline, and renders a 960×540 MP4. Its final line gives the exact output path. Pass `--voice "剣崎雌雄"` or `--voice "後鬼"` to try another installed voice; use `--style`, `--host`, `--port`, `--text`, and `--p2-text` as needed. If Japanese font discovery fails, add `--font-path "C:\path\to\NotoSansCJKjp-Regular.otf"`. Project schema v7 keeps old v1–v6 projects readable and does not delete legacy audio or media. The sentence WAVs live under `generated\audio\sentences\`; the renderer continues to use the assembled `generated\audio\<segment_id>.wav`. The default pause between sentences is 120 ms; set `project.sentence_pause_ms` or pass `--pause-ms` in the smoke command to change it.

For application code, call `SentenceSpeechService().sync_all(project, project_dir)`. Call `confirm_vietnamese_mapping(project, sentence_id, vietnamese_text)` only after reviewing the pairing; segmentation never guesses a Vietnamese sentence boundary. Captions may be edited separately from the text sent to VOICEVOX. `TimelineService.get_sentence_range(project, timeline, sentence_id)` returns the absolute spoken start/end milliseconds for a future KEY placement workflow.

## TTS-3 VOICEVOX KEY reveal demo

Start VOICEVOX Engine and run these commands in PowerShell from the repository:

```powershell
git switch te-v1-render-test
git pull origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
$env:TE_VOICEVOX_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_key_placement.py -q
Remove-Item Env:\TE_VOICEVOX_INTEGRATION
.\.venv\Scripts\python.exe -m te.tts.key_demo --voice "青山龍星" --target-order 2 --confirm-mapping --vn-text "Ohtani đánh cú home run lội ngược dòng!" --offset-ms 0
$video = Get-ChildItem ".\demo\test" -Recurse -Filter preview.mp4 | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
Start-Process $video
```

The demo prints the Japanese target, its parent Vietnamese segment, the spoken sentence interval, the KEY interval and the exact MP4 path. `--confirm-mapping` is an explicit editor action; `--vn-text` is the Vietnamese sentence you confirm, and `--target-order` selects the Japanese sentence within P2. The demo uses a blue/teal ordinary image before reveal and a bright pink KEY image at reveal. `--offset-ms` accepts positive or negative values within the target narration segment; `--duration-ms` sets an explicit visible duration. By default KEY lasts for the spoken sentence duration, capped at the end of its segment. Add `--font-path "C:\path\to\NotoSansCJKjp-Regular.otf"` if font discovery fails, or use the `TE_JAPANESE_FONT_PATH` environment variable. The demo writes only under `demo\test\` and never modifies personal media.

In application code, inspect a candidate with `KeyPlacementService.context(project, sentence_id)`, call `confirm_mapping(project, project_dir, sentence_id, vietnamese_text)` only after the editor verifies the pair, then mark a local image KEY with `MediaLibraryService.mark_key(...)` and call `assign_key(...)`. `resolve(...)` uses the current audio-driven timeline. `assign_random_images(...)` uses the project seed, excludes KEY media and retains saved assignments. If a sentence disappears or its confirmed mapping changes, the KEY placement becomes `manual_review`; an editor can confirm a new mapping and explicitly call `retarget_key(...)`. Project v8 migrates v1–v7 data without deleting old WAV or source media.

## TE Studio GUI Phase 1

Use the Python 3.12 Windows installer with Tcl/Tk enabled. From PowerShell in the repository:

```powershell
git switch te-v1-render-test
git pull origin te-v1-render-test
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui,test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m te.studio
```

To run the automated GUI launch/autosave/reopen smoke test on your desktop:

```powershell
$env:TE_STUDIO_GUI_SMOKE = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_studio.py -q
Remove-Item Env:\TE_STUDIO_GUI_SMOKE
```

The last command opens the Vietnamese editor. Create a video name, enter Vietnamese and Japanese scripts in P1–P4, choose this video's image folder, and save. Use **Thêm ảnh KEY** to select each KEY image; choose P1–P4, then select the Vietnamese sentence from the full-text list. TE suggests a Japanese sentence when the sentence counts match; review both sentences side by side, correct the Japanese dropdown if needed, and confirm. When counts differ, the editor shows “Cần chọn câu Nhật tương ứng” and waits for your choice. Existing KEY placements can be retargeted with the same Vietnamese-first workflow. VOICEVOX does not need to run while you edit. The **Dò lại VOICEVOX** button discovers installed speakers/styles when the local Engine is running.

Projects default to `D:\TE-Projects` and the future output folder to `D:\TE-Output` when drive D: exists. Otherwise the defaults are the current user's Documents/TE-Projects and Videos/TE-Output folders. **Thư mục mặc định** changes and remembers them. Channel profiles and these paths are saved under `%LOCALAPPDATA%\TEStudio\settings.json`; each project's editor settings are saved beside its project data in `studio.json`. The GUI saves after a short typing pause and also has an explicit **Lưu** button. Source images and videos remain at their original paths.

## TE Studio GUI Phase 2 — real narration and MP4 export

Open the locally installed VOICEVOX Engine before generating speech. In PowerShell, from the repository:

```powershell
git switch te-v1-render-test
git pull origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e ".[gui,test]"
.\.venv\Scripts\python.exe -m te.tts check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m te.studio
```

In the editor, open an existing project or enter Japanese narration in P1–P4, select this video's ordinary-image folder, optionally choose intros and confirm KEY pairs, then choose an installed VOICEVOX speaker/style. Click **Xuất Full HD 1920×1080** once: Studio checks inputs, generates only missing/stale sentence WAVs, updates sentence-timed Japanese subtitles, builds the timeline and renders the finished H.264/AAC MP4. **Xem nhanh 960×540** follows the same automatic preparation workflow. **Tạo / cập nhật lời đọc Nhật** remains an optional diagnostic button. Long Japanese sentences are automatically divided into consecutive captions of at most two lines; **Ký tự/dòng** controls their maximum line length without changing narration or KEY timing. Preview stays under the project's `generated\output\preview.mp4`; the final MP4 goes directly to the saved output directory (normally `D:\TE-Output`), using the video name and `_v2`, `_v3` when needed. Studio offers to open a completed Full HD video. **Mở bản xem nhanh**, **Mở video cuối** and **Mở thư mục xuất** remain available. The final output directory can be changed with **Thư mục mặc định**.

**Cỡ chữ phụ đề** sets the subtitle size in Full HD pixels (default 156); Preview scales it to half size. TE limits Japanese characters per line to the actual frame width, keeps at most two lines, and divides longer sentences into consecutive captions using the measured WAV timing. This editor setting is saved per project; older projects without the setting receive the new default. **Xáo trộn lại ảnh** changes the saved project seed and reassigns only unlocked automatic ordinary images. KEY images, intro clips, scripts and manual or locked assignments stay fixed. Rendering the same project again without clicking this button keeps its image sequence. New projects receive independent random seeds; existing projects with seed `0` keep their saved sequence until manually reshuffled.

Intro source audio is muted. FFmpeg now joins segment WAVs into one narration track at their timeline positions before mixing optional section music; P1–P4 intros remain visual overlays at the section boundary. After FFmpeg renders, TE compares several meaningful speech intervals against the expected narration WAV during each enabled intro and just after its boundary. The check allows AAC delay and an isolated low-correlation interval, but rejects actual silence, sustained missing speech, or an intro whose speech cannot be confirmed. A silent VOICEVOX WAV is regenerated. If verification fails, the error gives a path under the project's `generated\output\diagnostics\` folder. That folder retains `suspected-narration.mp4`, `suspected-interval.wav`, `narration-verification.json` (source/output levels and correlations), `ffmpeg-finalizing.log`, and `finalizing-plan.json` (the exact FFmpeg arguments, filter graph, audio block times, and intro times). The WAV and cached video clips remain available for a retry. Preview and Full HD use the same check. To exercise the synthetic-audio regression locally, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_narration_verification.py tests\test_intro_narration_audio.py tests\test_long_section_audio.py -q
```

For a failed render, copy the diagnostic path printed by TE Studio and inspect it in PowerShell:

```powershell
$diagnostic = 'D:\TE-Projects\YOUR-PROJECT\generated\output\diagnostics\YOUR-DIAGNOSTIC-FOLDER'
Get-Content (Join-Path $diagnostic 'narration-verification.json') -Encoding UTF8
ffprobe -v error -show_entries format=duration:stream=codec_name -of json (Join-Path $diagnostic 'suspected-narration.mp4')
Start-Process (Join-Path $diagnostic 'suspected-narration.mp4')
```

If font discovery fails, choose an installed Japanese `.otf`, `.ttf` or `.ttc` file in **File phông Nhật**. For example, Noto Sans CJK JP can be installed under `C:\Windows\Fonts` or `%LOCALAPPDATA%\Microsoft\Windows\Fonts`. The saved font path is project-local. The editor refuses stale narration, unavailable images/intros, unconfirmed KEYs and KEY/intro timing conflicts. The intro plays muted over the beginning of narration; it does not delay the voice. Image motion and section transition presets are applied during rendering. The GUI stays responsive and shows progress; only one production action runs at a time.

To inspect the finished file from PowerShell (replace the path with the one shown in TE Studio):

```powershell
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height -of json "D:\TE-Output\Tên video.mp4"
Start-Process "D:\TE-Output\Tên video.mp4"
```

To run the optional reproducible ~60-second fake-audio render benchmark:

```powershell
.\.venv\Scripts\python.exe -m te.studio.benchmark
```

Hardware H.264 encoding is selected only after a short actual encode probe. If NVENC, QSV or AMF is unavailable or the final encode fails, TE falls back to CPU `libx264`. Image filtering and intermediate clips still use CPU encoding. The current `crossfade` preset uses short fades at the section boundary while keeping the timeline length and KEY reveal timing fixed; a true overlapping frame blend is deferred. Render Queue and YouTube publishing remain for later phases. Cloud tests use fake local TTS; use the GUI steps above to verify real VOICEVOX speech on your Windows PC.
