"""Self-contained KEY reveal demo using real VOICEVOX by default."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
from uuid import uuid4

from te.media import KeyPlacementService, MediaLibraryService
from te.project import ProjectManager, SectionName
from te.project.models import TTSSettings
from te.render import RenderService, RenderSettings
from te.timeline import TimelineService
from te.tts.sentences import SentenceSpeechService


def run_demo(root: Path, *, provider: str = "voicevox", voice: str = "青山龍星",
             host: str = "127.0.0.1", port: int = 50021,
             vn_text: str, offset_ms: int = 0, duration_ms: int | None = None,
             font_path: str | None = None, target_order: int = 2) -> tuple[Path, int, int]:
    project_dir = root.resolve() / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    manager = ProjectManager()
    project = manager.create(project_dir, "TTS-3 KEY reveal")
    project.tts_settings = TTSSettings(provider=provider,
        voice_id=voice if provider == "voicevox" else "test",
        sample_rate=24000 if provider == "voicevox" else 16000, host=host, port=port)
    manager.add_segment(project, SectionName.P1, 1, "球場は静まり返りました。", "Sân vận động chìm trong im lặng.")
    reveal_segment = manager.add_segment(project, SectionName.P2, 2,
        "そして、運命の一球が投じられた。大谷が逆転のホームランを放った！",
        "Rồi cú ném định mệnh xuất hiện. Ohtani đánh cú home run lội ngược dòng!")
    project.random_seed = 42
    print(f"Project: {project_dir}", flush=True)
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    library = project_dir / "demo_media"
    normal = library / "normal"
    key_dir = library / "key"
    intro_dir = library / "section_intro"
    for folder in (normal, key_dir, intro_dir):
        folder.mkdir(parents=True, exist_ok=True)
    for target, color in ((normal / "P1.png", "0x193a64"),
                          (normal / "P2.png", "0x19574d"),
                          (key_dir / "reveal.png", "0xe73888")):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=640x360:d=0.1", "-frames:v", "1", str(target)], check=True)
    intro = intro_dir / "P1.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=0x101b31:s=640x360:r=30:d=0.6", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(intro)], check=True)
    MediaLibraryService().scan_library(project, library)
    by_name = {asset.filename: asset for asset in project.media}
    MediaLibraryService().assign_section_intro(project, SectionName.P1, by_name["P1.mp4"].id)
    manager.save(project_dir, project)
    KeyPlacementService.assign_random_images(project, project_dir)
    print(f"Generating {provider} sentence narration...", flush=True)
    SentenceSpeechService().sync_all(project, project_dir)
    target = next((unit for unit in project.sentence_units
                   if unit.segment_id == reveal_segment.id and unit.order == target_order), None)
    if target is None:
        raise ValueError(f"P2 has no Japanese sentence at order {target_order}")
    context = KeyPlacementService.context(project, target.id)
    print(f"Japanese target: {context.japanese_text}", flush=True)
    print(f"Vietnamese segment: {context.vietnamese_segment_text}", flush=True)
    KeyPlacementService.confirm_mapping(project, project_dir, target.id, vn_text)
    placement = KeyPlacementService.assign_key(project, project_dir, by_name["reveal.png"].id,
        target.id, offset_ms=offset_ms, duration_ms=duration_ms, locked=True)
    timeline_service = TimelineService()
    timeline, _ = timeline_service.rebuild_timeline(project, project_dir)
    timeline_service.set_transition(timeline, SectionName.P2, "fade")
    timeline_service.save_timeline(project, project_dir, timeline)
    spoken = timeline_service.get_sentence_range(project, timeline, target.id)
    start, end = KeyPlacementService.resolve(project, timeline, placement)
    print(f"Spoken sentence: {spoken[0]}-{spoken[1]} ms", flush=True)
    print(f"KEY reveal: {start}-{end} ms (offset {offset_ms} ms)", flush=True)
    assert all(item.media_id != placement.media_id or item.start_ms >= start
               for item in timeline.placements)
    print("Rendering KEY preview...", flush=True)
    output = RenderService().render_preview(project, timeline, project_dir,
        settings=RenderSettings(960, 540, 30, 28, "veryfast", font_path=font_path),
        progress=lambda value: print(f"  {value}", flush=True))
    print(f"Preview MP4: {output}", flush=True)
    return output, start, end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TE KEY image reveal demo")
    parser.add_argument("--root", type=Path, default=Path.cwd() / "demo" / "test")
    parser.add_argument("--provider", choices=("voicevox", "fake"), default="voicevox")
    parser.add_argument("--voice", default="青山龍星")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50021)
    parser.add_argument("--confirm-mapping", action="store_true",
                        help="explicitly approve the selected Vietnamese and Japanese sentence pair")
    parser.add_argument("--vn-text", required=True, help="Vietnamese text confirmed for the reveal sentence")
    parser.add_argument("--offset-ms", type=int, default=0)
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--target-order", type=int, default=2,
                        help="Japanese sentence order within P2 (1 or 2 in this demo)")
    parser.add_argument("--font-path")
    args = parser.parse_args(argv)
    if not args.confirm_mapping:
        parser.error("Pass --confirm-mapping only after reviewing the VN-JP pair in this demo")
    run_demo(args.root, provider=args.provider, voice=args.voice, host=args.host, port=args.port,
             vn_text=args.vn_text, offset_ms=args.offset_ms, duration_ms=args.duration_ms,
             font_path=args.font_path, target_order=args.target_order)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
