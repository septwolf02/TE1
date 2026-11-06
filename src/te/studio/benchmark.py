"""Optional reproducible ~60-second local render workload with fake TTS."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from time import perf_counter

from PIL import Image

from te.project import SectionName
from te.render import RenderService
from .rendering import StudioRenderService
from .service import StudioService


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a deterministic ~60-second TE Studio workload")
    parser.add_argument("--root", type=Path, default=Path.cwd() / "demo" / "test" / "studio-benchmark")
    parser.add_argument("--full", action="store_true", help="benchmark Full HD instead of preview")
    args = parser.parse_args()
    root = args.root.resolve()
    studio = StudioService(root / "settings.json")
    studio.set_directories(root / "projects", root / "output")
    state = studio.create_project("TE Studio benchmark")
    state.project.tts_settings.provider = "fake"
    state.project.tts_settings.voice_id = "local-benchmark"
    state.motion_preset = "none"
    studio.set_section(state, SectionName.P1, japanese="あ" * 600 + "。", vietnamese="")
    studio.save_project(state)
    library = root / "media"
    library.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        Image.new("RGB", (640, 360), (40 + index * 50, 70, 130)).save(library / f"image{index}.png")
    studio.select_media_folder(state, library)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state, print)
    started = perf_counter()
    output = workflow.render_video(state, preview=not args.full, progress=print)
    elapsed = perf_counter() - started
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_format", "-of", "json", str(output)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    print(f"Output: {output}")
    print(f"Video duration: {float(probe['format']['duration']):.2f}s; render wall time: {elapsed:.2f}s")
    print("Narration uses deterministic fake WAV; this does not benchmark VOICEVOX synthesis.")


if __name__ == "__main__":
    main()
