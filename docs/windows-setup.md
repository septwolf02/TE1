# Windows setup

1. Install Python 3.12 from python.org for your user account; administrator access is unnecessary.
2. Open PowerShell in the repository directory.
3. Create a virtual environment: `py -3.12 -m venv .venv`.
4. Install the package and test tools: `.venv\Scripts\python.exe -m pip install -e ".[test]"`.
5. Run tests: `.venv\Scripts\python.exe -m pytest`.

Create project directories outside this repository, such as `%USERPROFILE%\Videos\TE`. Register local images/videos by absolute path. The original files stay in place and are not modified.

For Module 6 rendering, install FFmpeg and ffprobe for your user account and add their directory to your user `PATH`. Confirm both with `ffmpeg -version` and `ffprobe -version` in a new PowerShell window. Install a Japanese-capable font such as Noto Sans CJK JP for your user account; configure `RenderSettings.font_family` if using a different family. Rendering does not need administrator privileges. Final MP4 files and previews are written under the project’s `generated/output/`, with temporary clips and logs under `generated/cache/render/`.
