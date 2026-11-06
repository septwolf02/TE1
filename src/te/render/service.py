"""Local FFmpeg renderer: normalized clips, concat, audio mix, ASS burn-in."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event
from typing import Callable

from te.project.manager import _write_json
from te.project.models import MediaType, Project
from te.subtitles import wrap_caption
from te.timeline.models import Timeline
from te.timeline.service import TimelineService


class RenderError(RuntimeError):
    def __init__(self, stage: str, command: list[str], stderr: str):
        super().__init__(f"Render failed during {stage}: {stderr[-600:]}")
        self.stage, self.command, self.stderr = stage, command, stderr


@dataclass(frozen=True)
class RenderSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    font_family: str = "Noto Sans CJK JP"
    font_path: str | None = None
    transition_ms: int = 250
    encoder: str = "libx264"


@dataclass(frozen=True)
class _VisualEvent:
    start_ms: int
    end_ms: int
    media_id: str | None
    effect: str = "none"
    video_policy: str = "trim_to_segment"
    is_key: bool = False


class RenderService:
    def __init__(self):
        self._cancel = Event()
        self._process: subprocess.Popen | None = None
        self._status = "idle"

    @staticmethod
    def check_ffmpeg() -> dict[str, str]:
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg and ffprobe must be installed and on PATH")
        version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=True).stdout.splitlines()[0]
        return {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "version": version}

    @staticmethod
    def choose_encoder(ffmpeg: str, *, timeout: float = 8.0) -> tuple[str, tuple[str, ...]]:
        """Actually encode a few frames; an encoder listing alone is insufficient."""
        warnings: list[str] = []
        try:
            available = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            return "libx264", (f"Không dò được bộ mã hóa phần cứng: {exc}",)
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            if not any(line.split()[1:2] == [encoder] for line in available.splitlines()):
                continue
            try:
                result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=128x72:r=30:d=0.15", "-frames:v", "4", "-an",
                    "-c:v", encoder, "-f", "null", "-"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout)
                if result.returncode == 0:
                    return encoder, tuple(warnings)
                warnings.append(f"{encoder}: {result.stderr.strip()[-180:] or 'encode test failed'}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"{encoder}: {exc}")
        return "libx264", tuple(warnings)

    def get_render_status(self) -> str:
        return self._status

    def cancel_render(self) -> None:
        self._cancel.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    @staticmethod
    def clear_cache(directory: Path) -> None:
        cache = Path(directory).resolve() / "generated" / "cache" / "render"
        if cache.is_dir():
            shutil.rmtree(cache)

    @staticmethod
    def _resolve_font(settings: RenderSettings) -> tuple[str, Path | None]:
        if settings.font_path:
            path = Path(settings.font_path)
            if not path.is_file():
                raise ValueError(f"Japanese font file unavailable: {path}")
            return settings.font_family, path.resolve()
        configured = os.environ.get("TE_JAPANESE_FONT_PATH")
        if configured:
            path = Path(configured)
            if not path.is_file():
                raise ValueError(f"Japanese font file unavailable: {path}")
            return settings.font_family, path.resolve()
        windows_dirs = []
        if local_app_data := os.environ.get("LOCALAPPDATA"):
            windows_dirs.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
        if windows_dir := os.environ.get("WINDIR"):
            windows_dirs.append(Path(windows_dir) / "Fonts")
        candidates = (
            ("NotoSansCJKjp*.otf", "Noto Sans CJK JP"),
            ("NotoSansCJK*.ttc", "Noto Sans CJK JP"),
            ("NotoSansJP*.ttf", "Noto Sans JP"),
            ("YuGoth*.ttc", "Yu Gothic"),
            ("meiryo*.ttc", "Meiryo"),
            ("msgothic.ttc", "MS Gothic"),
        )
        for pattern, family in candidates:
            for directory in windows_dirs:
                if directory.is_dir():
                    for path in sorted(directory.glob(pattern)):
                        if path.is_file():
                            return family, path.resolve()
        match = shutil.which("fc-match")
        if not match:
            raise ValueError("Japanese font unavailable; install one or set --font-path / TE_JAPANESE_FONT_PATH")
        result = subprocess.run([match, "-f", "%{family}|%{lang}", settings.font_family],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
        family, _, languages = result.partition("|")
        if "ja" not in languages or settings.font_family.casefold() not in family.casefold():
            raise ValueError(f"Japanese font unavailable: {settings.font_family}")
        return settings.font_family, None

    @staticmethod
    def _font(settings: RenderSettings) -> str:
        return RenderService._resolve_font(settings)[0]

    def validate_render_inputs(self, project: Project, timeline: Timeline, settings: RenderSettings) -> tuple[str, ...]:
        self.check_ffmpeg()
        if settings.width <= 0 or settings.height <= 0 or settings.fps <= 0 or not 0 <= settings.crf <= 51:
            raise ValueError("Invalid render settings")
        if settings.preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}:
            raise ValueError("Invalid encoding preset")
        if settings.transition_ms < 0:
            raise ValueError("Invalid transition duration")
        if settings.encoder not in {"auto", "libx264", "h264_nvenc", "h264_qsv", "h264_amf"}:
            raise ValueError("Invalid video encoder")
        if timeline.captions:
            self._font(settings)
        warnings = TimelineService().validate_timeline(project, timeline)
        for block in timeline.blocks:
            if not Path(block.audio_path).is_file():
                raise ValueError(f"Narration WAV missing: {block.audio_path}")
        if not timeline.blocks:
            raise ValueError("Timeline has no narration blocks")
        media = {asset.id: asset for asset in project.media}
        for event in timeline.intros:
            if not Path(media[event.media_id].path).is_file():
                raise ValueError(f"Intro video missing: {event.media_id}")
        for block in timeline.blocks:
            if block.media_id and block.visual_status == "ready" and not Path(media[block.media_id].path).is_file():
                raise ValueError(f"Visual file missing: {block.media_id}")
        for placement in timeline.placements:
            if not Path(media[placement.media_id].path).is_file():
                raise ValueError(f"Visual file missing: {placement.media_id}")
        for music in timeline.music:
            if not Path(music.path).is_file():
                raise ValueError(f"Music file missing: {music.path}")
        return warnings

    @staticmethod
    def _visual_events(project: Project, timeline: Timeline) -> list[tuple[int, int, str, object]]:
        key_ids = {placement.id for placement in project.key_placements}
        placements = sorted(timeline.placements, key=lambda item: (item.start_ms, item.end_ms))
        if any(first.end_ms > second.start_ms for first, second in zip(placements, placements[1:])):
            raise ValueError("Visual placements overlap")
        for key in project.key_placements:
            if key.status != "ready":
                continue
            placement = next((item for item in placements if item.id == key.id), None)
            if placement and any(placement.start_ms < intro.end_ms and intro.start_ms < placement.end_ms
                                 for intro in timeline.intros):
                raise ValueError("KEY image overlaps an intro video")
        end_ms = max([block.end_ms for block in timeline.blocks] +
                     [intro.end_ms for intro in timeline.intros], default=0)
        points = sorted({0, end_ms} |
            {value for item in placements for value in (item.start_ms, item.end_ms)} |
            {value for item in timeline.intros for value in (item.start_ms, item.end_ms)})
        events: list[tuple[int, int, str, object]] = []
        for start, end in zip(points, points[1:]):
            if end <= start:
                continue
            intros = [item for item in timeline.intros if item.start_ms <= start and end <= item.end_ms]
            if len(intros) > 1:
                raise ValueError("Intro videos overlap")
            if intros:
                events.append((start, end, "intro", intros[0]))
                continue
            placement = next((item for item in placements if item.start_ms <= start and end <= item.end_ms), None)
            if placement:
                effect = placement.effects[0].get("name", "none") if placement.effects else "none"
                video_policy = next((block.video_policy for block in timeline.blocks
                    if block.segment_id == placement.segment_id), "trim_to_segment")
                visual = _VisualEvent(start, end, placement.media_id, effect,
                    video_policy, placement.id in key_ids)
            else:
                visual = _VisualEvent(start, end, None)
            events.append((start, end, "visual", visual))
        return events

    def _run(self, command: list[str], stage: str, log_dir: Path) -> None:
        if self._cancel.is_set():
            raise RenderError(stage, command, "Cancelled")
        self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, stderr_bytes = self._process.communicate()
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            (log_dir / f"{stage}.log").write_text(stderr, encoding="utf-8")
            if self._cancel.is_set() or self._process.returncode != 0:
                raise RenderError(stage, command, "Cancelled" if self._cancel.is_set() else stderr)
        finally:
            self._process = None

    @staticmethod
    def _visual_filter(effect: str, width: int, height: int, fps: int, frames: int) -> str:
        base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
        if effect == "none":
            return base + f",fps={fps}"
        if effect.startswith("slow_zoom"):
            z = f"1+0.08*on/{max(1, frames - 1)}" if effect.endswith("in") else f"1.08-0.08*on/{max(1, frames - 1)}"
            return base + f",zoompan=z='{z}':d=1:s={width}x{height}:fps={fps}"
        direction = effect.removeprefix("pan_")
        factor = f"on/{max(1, frames - 1)}"
        x = f"(iw-iw/zoom)*{factor}" if direction == "right" else f"(iw-iw/zoom)*(1-{factor})" if direction == "left" else "(iw-iw/zoom)/2"
        y = f"(ih-ih/zoom)*{factor}" if direction == "down" else f"(ih-ih/zoom)*(1-{factor})" if direction == "up" else "(ih-ih/zoom)/2"
        return base + f",zoompan=z=1.08:x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"

    @staticmethod
    def _ass_time(ms: int) -> str:
        cs = round(ms / 10)
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02}:{s:02}.{cs:02}"

    @classmethod
    def _write_ass(cls, project: Project, timeline: Timeline, path: Path, settings: RenderSettings, font: str) -> None:
        style = project.subtitle_style
        size = max(18, round(settings.height * 0.048))
        def ass_color(value: str, fallback: str) -> str:
            if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
                return fallback
            try:
                int(value[1:], 16)
            except ValueError:
                return fallback
            return f"&H00{value[5:7]}{value[3:5]}{value[1:3]}".upper()
        text_color = ass_color(style.get("text_color", ""), "&H00FFFFFF")
        outline_color = ass_color(style.get("outline_color", ""), "&H00000000")
        header = (
            "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
            f"PlayResX: {settings.width}\nPlayResY: {settings.height}\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Normal,{font},{size},{text_color},{text_color},{outline_color},&H88000000,-1,0,0,0,100,100,0,0,3,2,0,2,60,60,55,1\n"
            f"Style: Headline,{font},{round(size*1.6)},{text_color},{text_color},{outline_color},&H00000000,-1,0,0,0,100,100,0,0,1,3,0,5,80,80,40,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )
        by_id = {c.id: c for c in project.subtitle_captions}
        lines = [header]
        for caption in timeline.captions:
            source = by_id.get(caption.id)
            headline = bool(source and source.presentation == "headline" and timeline.headline.get("enabled"))
            text = caption.text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
            if not headline:
                text = "\\N".join(wrap_caption(text, style["max_chars_per_line"], style["max_lines"]))
            else:
                dark = round((1 - timeline.headline.get("darkening", 0.7)) * 255)
                text = f"{{\\an5\\bord3\\shad0}}{text}"
                lines.append(f"Dialogue: -1,{cls._ass_time(caption.start_ms)},{cls._ass_time(caption.end_ms)},Headline,,0,0,0,,{{\\an7\\bord0\\shad0\\1c&H000000&\\1a&H{dark:02X}&\\p1}}m 0 0 l {settings.width} 0 {settings.width} {settings.height} 0 {settings.height}{{\\p0}}\n")
                lines.append(f"Dialogue: 0,{cls._ass_time(caption.start_ms)},{cls._ass_time(caption.end_ms)},Headline,,0,0,0,,{{\\alpha&H00&}}{text}\n")
                continue
            lines.append(f"Dialogue: 0,{cls._ass_time(caption.start_ms)},{cls._ass_time(caption.end_ms)},Normal,,0,0,0,,{text}\n")
        path.write_text("".join(lines), encoding="utf-8")

    def render_preview(self, project: Project, timeline: Timeline, directory: Path, **kwargs) -> Path:
        return self.render_project(project, timeline, directory, preview=True, **kwargs)

    def render_project(self, project: Project, timeline: Timeline, directory: Path, *,
                       preview: bool = False, settings: RenderSettings | None = None,
                       overwrite: bool = False, progress: Callable[[str], None] | None = None) -> Path:
        settings = settings or (RenderSettings(960, 540, 30, 28, "veryfast") if preview else RenderSettings())
        directory = Path(directory).resolve()
        self._cancel.clear()
        self._status = "preparing"
        def emit(value: str) -> None:
            self._status = value
            if progress:
                progress(value)
        info = self.check_ffmpeg()
        self.validate_render_inputs(project, timeline, settings)
        encoder = settings.encoder
        if encoder == "auto":
            encoder, reasons = self.choose_encoder(info["ffmpeg"])
            for reason in reasons:
                emit(f"Bộ mã hóa phần cứng không khả dụng: {reason}")
        emit(f"Bộ mã hóa xuất video: {encoder}")
        cache = directory / "generated" / "cache" / "render"
        output_dir = directory / "generated" / "output"
        cache.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / ("preview.mp4" if preview else "final.mp4")
        if output.exists() and not overwrite:
            raise FileExistsError(output)
        partial = output_dir / ("preview.partial.mp4" if preview else "final.partial.mp4")
        manifest = output_dir / ("preview.manifest.json" if preview else "final.manifest.json")
        events = self._visual_events(project, timeline)
        media = {asset.id: asset for asset in project.media}
        transitions = {event.at_ms: event.preset for event in timeline.transitions}
        key_starts = {start for start, _, kind, event in events
                      if kind == "visual" and event.is_key}
        def frame_at(ms: int) -> int:
            position = ms * settings.fps / 1000
            return math.ceil(position) if ms in key_starts else round(position)
        used: list[Path] = []
        stage = "preparing"
        try:
            for index, (start, end, kind, event) in enumerate(events, 1):
                stage = f"segment-{index}"
                emit(f"rendering segment {index}/{len(events)}")
                asset = media.get(event.media_id)
                duration = (end - start) / 1000
                frames = frame_at(end) - frame_at(start)
                if frames <= 0:
                    if kind == "visual" and event.is_key:
                        raise ValueError("KEY duration is shorter than one rendered frame")
                    continue
                clip_settings = {key: getattr(settings, key) for key in
                    ("width", "height", "fps", "crf", "preset", "transition_ms")}
                signature = json.dumps([kind, asdict(event), clip_settings, info["version"],
                    transitions.get(start), transitions.get(end), settings.transition_ms, frames,
                    (asset.path, Path(asset.path).stat().st_size, Path(asset.path).stat().st_mtime_ns) if asset and Path(asset.path).exists() else None], sort_keys=True, default=str)
                clip = cache / f"{sha256(signature.encode()).hexdigest()}.mp4"
                if not clip.exists():
                    effect = event.effect if kind == "visual" else "none"
                    filter_text = self._visual_filter(effect, settings.width, settings.height, settings.fps, frames)
                    fade_duration = min(settings.transition_ms / 1000, duration / 2)
                    if transitions.get(start) in ("fade", "crossfade") and fade_duration:
                        filter_text += f",fade=t=in:st=0:d={fade_duration}"
                    if transitions.get(end) in ("fade", "crossfade") and fade_duration:
                        filter_text += f",fade=t=out:st={duration-fade_duration}:d={fade_duration}"
                    if kind == "visual" and asset is None:
                        command = [info["ffmpeg"], "-y", "-f", "lavfi", "-i", f"color=c=black:s={settings.width}x{settings.height}:r={settings.fps}:d={(frames + 1)/settings.fps}"]
                    elif asset.media_type == MediaType.IMAGE:
                        command = [info["ffmpeg"], "-y", "-loop", "1", "-framerate", str(settings.fps), "-i", asset.path]
                    else:
                        if (kind == "visual" and event.video_policy in ("trim_to_segment", "keep_original")
                                and asset.duration_seconds is not None and asset.duration_seconds + 0.001 < duration):
                            raise ValueError("Video is shorter than segment; use loop_to_segment or freeze_last_frame")
                        command = [info["ffmpeg"], "-y"]
                        if kind == "visual" and event.video_policy == "loop_to_segment":
                            command += ["-stream_loop", "-1"]
                        command += ["-i", asset.path]
                        if kind == "visual" and event.video_policy == "freeze_last_frame":
                            filter_text += f",tpad=stop_mode=clone:stop_duration={duration}"
                    command += ["-an", "-vf", filter_text, "-frames:v", str(frames), "-r", str(settings.fps),
                        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                        "-pix_fmt", "yuv420p", str(clip)]
                    self._run(command, stage, cache)
                used.append(clip)
            concat_list = cache / "concat.txt"
            concat_list.write_text("".join(f"file '{str(path).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n" for path in used), encoding="utf-8")
            visual = cache / "visual.mp4"
            self._run([info["ffmpeg"], "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                       "-c", "copy", str(visual)], "concatenating", cache)
            stage = "finalizing"
            emit("mixing audio")
            command = [info["ffmpeg"], "-y", "-i", str(visual)]
            filters = []
            audio_labels = []
            input_index = 1
            for block in timeline.blocks:
                command += ["-i", block.audio_path]
                filters.append(f"[{input_index}:a]adelay={block.start_ms}:all=1[n{input_index}]")
                audio_labels.append(f"[n{input_index}]")
                input_index += 1
            for music in timeline.music:
                section = TimelineService.get_section_range(timeline, music.section)
                if not section:
                    continue
                command += ["-stream_loop", "-1", "-i", music.path]
                duration = (section[1] - section[0]) / 1000
                filters.append(f"[{input_index}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume={music.volume},"
                               f"afade=t=in:st=0:d={music.fade_in_ms/1000},"
                               f"afade=t=out:st={max(0,duration-music.fade_out_ms/1000)}:d={music.fade_out_ms/1000},"
                               f"adelay={section[0]}:all=1[m{input_index}]")
                audio_labels.append(f"[m{input_index}]")
                input_index += 1
            filters.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0[aout]")
            ass = cache / "captions.ass"
            if timeline.captions:
                emit("burning subtitles")
                font_family, font_path = self._resolve_font(settings)
                self._write_ass(project, timeline, ass, settings, font_family)
                # Filter path is generated under the project and passed as one argument.
                filter_path = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                font_dir = ""
                if font_path:
                    directory_path = str(font_path.parent).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                    font_dir = f":fontsdir='{directory_path}'"
                filters.append(f"[0:v]ass=filename='{filter_path}'{font_dir}[vout]")
                video_map = "[vout]"
            else:
                video_map = "0:v"
            emit("finalizing")
            command += ["-filter_complex", ";".join(filters), "-map", video_map, "-map", "[aout]",
                        "-c:v", encoder]
            if encoder == "libx264":
                command += ["-preset", settings.preset, "-crf", str(settings.crf)]
            else:
                command += ["-b:v", "8M" if settings.width >= 1920 else "3M"]
            command += [
                        "-pix_fmt", "yuv420p", "-r", str(settings.fps), "-c:a", "aac", "-b:a", "192k",
                        "-t", str(max(end for _, end, _, _ in events) / 1000), "-movflags", "+faststart", str(partial)]
            try:
                self._run(command, stage, cache)
            except RenderError:
                if encoder == "libx264" or self._cancel.is_set():
                    raise
                emit(f"{encoder} thất bại khi xuất; thử lại bằng CPU libx264")
                partial.unlink(missing_ok=True)
                cpu = command[:]
                codec_index = cpu.index("-c:v", cpu.index("-filter_complex"))
                cpu[codec_index:codec_index + 2] = ["-c:v", "libx264", "-preset", settings.preset,
                    "-crf", str(settings.crf)]
                bitrate_index = cpu.index("-b:v")
                del cpu[bitrate_index:bitrate_index + 2]
                self._run(cpu, stage, cache)
                encoder = "libx264"
            os.replace(partial, output)
            emit("completed")
            return output
        except Exception:
            self._status = "failed" if not self._cancel.is_set() else "cancelled"
            partial.unlink(missing_ok=True)
            raise
        finally:
            _write_json(manifest, {
                "project_id": project.id, "timeline_version": timeline.schema_version,
                "timeline_hash": sha256(json.dumps(timeline.to_dict(), sort_keys=True, default=str).encode()).hexdigest(),
                "rendered_at": datetime.now(timezone.utc).isoformat(), "output_path": str(output),
                "resolution": [settings.width, settings.height], "fps": settings.fps,
                "video_codec": "h264", "encoder_used": encoder,
                "audio_codec": "aac", "pixel_format": "yuv420p",
                "settings": asdict(settings), "ffmpeg_version": info["version"], "status": self._status,
                "stage": stage,
            })
