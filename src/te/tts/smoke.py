"""Self-contained sentence speech -> timeline -> preview MP4 smoke workflow."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
from uuid import uuid4

from te.media import MediaAssignmentService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.render import RenderService, RenderSettings
from te.timeline import TimelineService
from .sentences import SentenceSpeechService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TE sentence-level TTS and render smoke test")
    parser.add_argument("--root", type=Path, default=Path.cwd() / "demo" / "test")
    parser.add_argument("--provider", choices=("voicevox", "fake"), default="voicevox")
    parser.add_argument("--voice", default="青山龍星")
    parser.add_argument("--style")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50021)
    parser.add_argument("--text", default="大谷選手が打席に立ちました。球場が静まり返ります。")
    parser.add_argument("--p2-text", default="そして、運命の一球が投じられました。")
    parser.add_argument("--pause-ms", type=int, default=120)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--intonation", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument("--font-path")
    args = parser.parse_args(argv)
    project_dir = args.root.resolve() / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    manager = ProjectManager()
    project = manager.create(project_dir, "TTS-2 smoke")
    project.tts_settings = TTSSettings(provider=args.provider,
        voice_id=args.voice if args.provider == "voicevox" else "test", style=args.style,
        sample_rate=24000 if args.provider == "voicevox" else 16000,
        host=args.host, port=args.port, speaking_rate=args.speed, pitch=args.pitch,
        intonation=args.intonation, volume=args.volume)
    project.sentence_pause_ms = args.pause_ms
    texts = [(SectionName.P1, args.text), (SectionName.P2, args.p2_text)]
    for index, (section, text) in enumerate(texts, 1):
        manager.add_segment(project, section, index, text)
    image_dir = project_dir / "demo_media" / "normal"
    image_dir.mkdir(parents=True)
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    for index in (1, 2):
        image = image_dir / f"image{index}.png"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={'0x193a64' if index == 1 else '0x78364a'}:s=640x360:d=0.1",
                        "-frames:v", "1", str(image)], check=True)
    MediaLibraryService().scan_library(project, image_dir.parent)
    media = {asset.filename: asset for asset in project.media}
    for index, segment in enumerate(project.segments, 1):
        MediaAssignmentService().assign_media(project, segment.id, media[f"image{index}.png"].id, locked=True)
    manager.save(project_dir, project)
    print(f"Project: {project_dir}", flush=True)
    print(f"Generating {args.provider} sentence WAV files...", flush=True)
    speech = SentenceSpeechService()
    anchors = speech.sync_all(project, project_dir)
    timeline = TimelineService.load_timeline(project, project_dir)
    for segment in project.segments:
        for unit, start, end in anchors[segment.id]:
            absolute = TimelineService.get_sentence_range(project, timeline, unit.id)
            print(f"{unit.id}: segment {segment.number}, {start}-{end} ms; timeline {absolute[0]}-{absolute[1]} ms; {unit.synthesis_text}", flush=True)
    print("Rendering preview...", flush=True)
    output = RenderService().render_preview(project, timeline, project_dir,
        settings=RenderSettings(960, 540, 30, 28, "veryfast", font_path=args.font_path),
        progress=lambda value: print(f"  {value}", flush=True))
    print(f"Preview MP4: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
