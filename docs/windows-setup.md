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
