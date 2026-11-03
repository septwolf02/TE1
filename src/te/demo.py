"""Self-contained local demo for Modules 0–6.

Run with `python -m te.demo`; add `--full` for a 1080p render.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
from uuid import uuid4

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import AssignmentType, ProjectManager, SectionName
from te.render import RenderService, RenderSettings
from te.subtitles import SubtitleService
from te.timeline import TimelineService
from te.tts import TTSService


SCRIPT = (
    (SectionName.P1, "その一球が、球場の空気を変えた。"),
    (SectionName.P1, "誰もが次の瞬間を待っていた。"),
    (SectionName.P1, "そして、ついに秘密が明かされる！"),
    (SectionName.P2, "ここから試合は新しい局面に入る。"),
    (SectionName.P2, "選手たちは最後まで諦めなかった。"),
    (SectionName.P2, "この一日が、長く語り継がれる。"),
)
COLORS = ("0x173B65", "0x285B73", "0x3A6E64", "0xAA5C3D", "0x7A3E60", "0x5E547A")
EFFECTS = ("slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "slow_zoom_in", "slow_zoom_out")


def _ffmpeg(command: list[str], *, label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Could not generate {label}: {result.stderr[-1000:]}")


def run_demo(root: Path, *, full: bool = False, font_family: str = "Noto Sans CJK JP",
             font_path: str | None = None) -> tuple[Path, Path | None]:
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    run_name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    project_dir = Path(root).resolve() / run_name
    manager = ProjectManager()
    project = manager.create(project_dir, "TE V1 local demo")
    library_dir = project_dir / "demo_media"
    normal_dir = library_dir / "normal"
    key_dir = library_dir / "key"
    intro_dir = library_dir / "section_intro"
    for directory in (normal_dir, key_dir, intro_dir):
        directory.mkdir(parents=True, exist_ok=True)

    print(f"Demo project: {project_dir}", flush=True)
    print("Generating six images, P1 intro video and background music...", flush=True)
    for index, color in enumerate(COLORS, 1):
        image_dir = key_dir if index == 3 else normal_dir
        image = image_dir / f"image{index:03}.png"
        _ffmpeg([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                 "-i", f"color=c={color}:s=640x360:d=0.1", "-frames:v", "1", str(image)],
                label=image.name)
    intro = intro_dir / "P1.mp4"
    _ffmpeg([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "color=c=0x101B31:s=640x360:r=30:d=0.6", "-an", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(intro)], label="P1 intro")
    music = project_dir / "generated" / "audio" / "demo_music.wav"
    music.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=220:sample_rate=16000:duration=12", "-af", "volume=0.15",
             "-ac", "1", str(music)], label="background music")

    for number, (section, text) in enumerate(SCRIPT, 1):
        manager.add_segment(project, section, number, text)
    library = MediaLibraryService()
    library.scan_library(project, library_dir)
    by_name = {asset.filename: asset for asset in project.media}
    library.assign_section_intro(project, SectionName.P1, by_name["P1.mp4"].id)
    assignments = MediaAssignmentService()
    for index, segment in enumerate(project.segments, 1):
        image = by_name[f"image{index:03}.png"]
        assignments.assign_media(project, segment.id, image.id,
                                 assignment_type=AssignmentType.KEY if index == 3 else AssignmentType.MANUAL,
                                 locked=index == 3, effect_preference=EFFECTS[index - 1])
    manager.save(project_dir, project)

    print("Generating fake local TTS for six segments...", flush=True)
    records = TTSService().generate_all(project, project_dir)
    if any(record.status != "ready" for record in records):
        raise RuntimeError("Demo TTS generation failed")
    print("Splitting and aligning Japanese captions...", flush=True)
    subtitle_results = SubtitleService().align_all(project, project_dir)
    if any(status not in ("aligned", "approximate") for status in subtitle_results.values()):
        raise RuntimeError(f"Demo subtitle alignment failed: {subtitle_results}")

    timeline_service = TimelineService()
    timeline, warnings = timeline_service.build_timeline(project, project_dir)
    if warnings:
        print("Timeline warnings: " + "; ".join(warnings), flush=True)
    timeline_service.set_transition(timeline, SectionName.P2, "fade")
    timeline_service.assign_section_music(timeline, SectionName.P1, music, volume=0.12,
                                          fade_in_ms=300, fade_out_ms=300)
    timeline_service.set_headline_config(timeline, darkening=0.7)
    timeline_service.save_timeline(project, project_dir, timeline)

    render = RenderService()
    print("Rendering 960x540 preview...", flush=True)
    preview = render.render_preview(project, timeline, project_dir,
                                    settings=RenderSettings(960, 540, 30, 28, "veryfast",
                                                            font_family=font_family, font_path=font_path),
                                    progress=lambda message: print(f"  {message}", flush=True))
    print(f"Preview MP4: {preview}", flush=True)
    full_output = None
    if full:
        print("Rendering 1920x1080 full video...", flush=True)
        full_output = render.render_project(project, timeline, project_dir,
                                            settings=RenderSettings(font_family=font_family, font_path=font_path),
                                            progress=lambda message: print(f"  {message}", flush=True))
        print(f"Full MP4: {full_output}", flush=True)
    return preview, full_output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and render a self-contained TE V1 demo")
    parser.add_argument("--full", action="store_true", help="also render the 1920x1080 MP4")
    parser.add_argument("--root", type=Path, default=Path.cwd() / "demo" / "test",
                        help="parent directory for a new demo project")
    parser.add_argument("--font-family", default="Noto Sans CJK JP", help="installed Japanese font family")
    parser.add_argument("--font-path", help="path to a Japanese font file, if Fontconfig is unavailable")
    args = parser.parse_args()
    run_demo(args.root, full=args.full, font_family=args.font_family, font_path=args.font_path)


if __name__ == "__main__":
    main()
