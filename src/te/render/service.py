"""Local FFmpeg renderer: normalized clips, concat, audio mix, ASS burn-in."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from array import array
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import wave
from threading import Event
from typing import Callable
from uuid import uuid4

from te.project.manager import _write_json
from te.project.models import MediaType, Project
from te.subtitles import wrap_caption, split_caption_text
from te.timeline.models import Timeline
from te.timeline.service import TimelineService


class RenderError(RuntimeError):
    def __init__(self, stage: str, command: list[str], stderr: str):
        super().__init__(f"Render failed during {stage}: {stderr[-600:]}")
        self.stage, self.command, self.stderr = stage, command, stderr


class NarrationVerificationError(ValueError):
    def __init__(self, section: str, reason: str, windows: list[dict], source_paths: list[str]):
        detail = ("MP4 thực sự im lặng" if reason == "output_silent" else
                  "WAV không có khoảng lời nói rõ" if reason == "source_silent" else
                  "không xác nhận được lời đọc trong MP4")
        super().__init__(f"Intro {section}: {detail}")
        self.section, self.reason, self.windows, self.source_paths = section, reason, windows, source_paths


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
    subtitle_font_size: int = 156  # Full-HD reference pixels; scales with output height.


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

    @staticmethod
    def _pcm_slice(ffmpeg: str, path: Path, start_ms: int, duration_ms: int) -> array:
        result = subprocess.run([ffmpeg, "-v", "error", "-ss", str(max(0, start_ms) / 1000),
            "-i", str(path), "-t", str(duration_ms / 1000), "-vn", "-ac", "1", "-ar", "8000",
            "-f", "s16le", "-"], capture_output=True, check=True)
        samples = array("h")
        samples.frombytes(result.stdout[:len(result.stdout) // 2 * 2])
        return samples

    @staticmethod
    def _voice_score(source: array, output: array, expected: int, max_shift: int = 1600) -> tuple[float, int]:
        """Best normalized waveform match, allowing AAC priming and local timing drift."""
        if len(source) < 1200:
            return 0.0, 0
        source_energy = sum(value * value for value in source)
        if source_energy / len(source) < 50 * 50:
            return 0.0, 0
        best, best_shift = 0.0, 0
        for shift in range(-max_shift, max_shift + 1, 80):
            start = expected + shift
            if start < 0 or start + len(source) > len(output):
                continue
            candidate = output[start:start + len(source)]
            output_energy = sum(value * value for value in candidate)
            if not output_energy:
                continue
            dot = sum(a * b for a, b in zip(source, candidate))
            score = dot / math.sqrt(source_energy * output_energy)
            if score > best:
                best, best_shift = score, shift
        return best, best_shift

    @staticmethod
    def _similar_voice(source: array, output: array) -> bool:
        return RenderService._voice_score(source, output, 0, min(1600, max(0, len(output) - len(source))))[0] >= 0.12

    @staticmethod
    def _rms(samples: array) -> float:
        return math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0

    def _verify_intro_narration(self, project: Project, timeline: Timeline, output: Path, ffmpeg: str) -> None:
        """Check speech energy and several longer AAC matches, allowing an isolated mismatch."""
        segments = {segment.id: segment for segment in project.segments}
        for intro in timeline.intros:
            section_end = max(block.end_ms for block in timeline.blocks if block.section == intro.section)
            check_end = min(section_end, intro.end_ms + 300)
            pad = min(250, intro.start_ms)
            decoded_start = intro.start_ms - pad
            audible_all = self._pcm_slice(ffmpeg, output, decoded_start,
                                          check_end - decoded_start + 250)
            results: list[dict] = []
            source_paths: list[str] = []
            real_blocks = 0
            for block in timeline.blocks:
                segment = segments[block.segment_id]
                if block.section != intro.section:
                    continue
                if (segment.tts_override or project.tts_settings).provider == "fake":
                    continue  # The deterministic fake provider deliberately generates silence.
                real_blocks += 1
                overlap_start = max(intro.start_ms, block.start_ms)
                overlap_end = min(check_end, block.end_ms)
                if overlap_end - overlap_start < 300:
                    continue
                source_paths.append(block.audio_path)
                source_all = self._pcm_slice(ffmpeg, Path(block.audio_path),
                    overlap_start - block.start_ms, overlap_end - overlap_start)
                for start in range(overlap_start, overlap_end - 299, 500):
                    duration = min(700, overlap_end - start)
                    source_offset = round((start - overlap_start) * 8)
                    output_offset = round((start - decoded_start) * 8)
                    source = source_all[source_offset:source_offset + duration * 8]
                    source_rms = self._rms(source)
                    if source_rms < 150:
                        continue
                    audible = audible_all[output_offset:output_offset + duration * 8]
                    output_rms = self._rms(audible)
                    score, shift = self._voice_score(source, audible_all, output_offset)
                    results.append({"start_ms": start, "duration_ms": duration,
                        "source_rms": round(source_rms, 1), "output_rms": round(output_rms, 1),
                        "correlation": round(score, 3), "shift_ms": round(shift / 8),
                        "matched": score >= 0.12})
            if not real_blocks:
                continue
            if not results:
                raise NarrationVerificationError(intro.section.value, "source_silent", results, source_paths)
            # Near-zero output is stronger evidence than correlation: fail even for one long interval.
            if any(item["output_rms"] < max(50, item["source_rms"] * 0.04) for item in results):
                raise NarrationVerificationError(intro.section.value, "output_silent", results, source_paths)
            matches = sum(item["matched"] for item in results)
            longest_miss = miss = 0
            for item in results:
                miss = 0 if item["matched"] else miss + 1
                longest_miss = max(longest_miss, miss)
            # Two adjacent 700 ms speech windows indicate a sustained loss; one
            # mismatched window can be an AAC/music artefact and is diagnostic only.
            if matches == 0 or (len(results) >= 3 and
                                (longest_miss >= 2 or matches / len(results) < 0.35)):
                raise NarrationVerificationError(intro.section.value, "low_similarity", results, source_paths)

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
        if not 36 <= settings.subtitle_font_size <= 240:
            raise ValueError("Subtitle font size must be between 36 and 240")
        if timeline.captions:
            self._font(settings)
        warnings = TimelineService().validate_timeline(project, timeline)
        segments = {segment.id: segment for segment in project.segments}
        for block in timeline.blocks:
            if not Path(block.audio_path).is_file():
                raise ValueError(f"Narration WAV missing: {block.audio_path}")
            if (segments[block.segment_id].tts_override or project.tts_settings).provider != "fake":
                with wave.open(block.audio_path, "rb") as source:
                    if source.getsampwidth() != 2:
                        raise ValueError(f"WAV lời đọc không phải PCM 16-bit: {block.audio_path}")
                    samples = array("h", source.readframes(source.getnframes()))
                if not samples or sum(value * value for value in samples) / len(samples) < 50 * 50:
                    raise ValueError(f"WAV lời đọc không có âm thanh: {block.audio_path}")
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
            if stage in {"audio-mixing", "muxing"} and any(phrase in stderr.casefold() for phrase in
                    ("non-monotonic dts", "non-monotonous dts", "queue input is backward in time")):
                raise RenderError(stage, command, "FFmpeg reported backward timestamps: " + stderr)
        finally:
            self._process = None

    @staticmethod
    def _verify_packet_timestamps(ffprobe: str, path: Path, stream: str = "a:0") -> None:
        result = subprocess.run([ffprobe, "-v", "error", "-select_streams", stream,
            "-show_entries", "packet=dts", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        previous = None
        count = 0
        for line in result.stdout.splitlines():
            if not line or line == "N/A":
                continue
            current = int(line.split(",", 1)[0])
            if previous is not None and current <= previous:
                raise ValueError(f"AAC/video DTS đi lùi tại gói {count}: {previous} -> {current} ({path})")
            previous = current
            count += 1
        if count == 0:
            raise ValueError(f"Không tìm thấy gói {stream} trong {path}")

    @staticmethod
    def _keep_narration_diagnostic(partial: Path, output_dir: Path, cache: Path,
                                   ffmpeg: str, error: Exception) -> Path:
        folder = output_dir / "diagnostics" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                                  + "-" + uuid4().hex[:8])
        folder.mkdir(parents=True)
        video = folder / "suspected-narration.mp4"
        os.replace(partial, video)
        windows = error.windows if isinstance(error, NarrationVerificationError) else []
        _write_json(folder / "narration-verification.json", {
            "error": str(error), "reason": getattr(error, "reason", "verification_error"),
            "section": getattr(error, "section", None), "windows": windows,
            "source_wavs": getattr(error, "source_paths", []),
            "video": str(video), "sample_rate_hz": 8000,
            "correlation_threshold": 0.12,
        })
        final_log = cache / "finalizing.log"
        if final_log.is_file():
            shutil.copyfile(final_log, folder / "ffmpeg-finalizing.log")
        for name in ("audio-mixing.log", "muxing.log"):
            log = cache / name
            if log.is_file():
                shutil.copyfile(log, folder / f"ffmpeg-{name}")
        plan = cache / "finalizing-plan.json"
        if plan.is_file():
            shutil.copyfile(plan, folder / "finalizing-plan.json")
        if windows:
            suspect = next((item for item in windows if not item["matched"]), windows[0])
            excerpt = folder / "suspected-interval.wav"
            result = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss",
                str(max(0, suspect["start_ms"] - 400) / 1000), "-i", str(video),
                "-t", "2", "-vn", "-c:a", "pcm_s16le", str(excerpt)],
                capture_output=True)
            (folder / "audio-excerpt.log").write_text(
                result.stderr.decode("utf-8", errors="replace"), encoding="utf-8")
        return video

    @staticmethod
    def _visual_filter(effect: str, width: int, height: int, fps: int, frames: int) -> str:
        base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
        finish = ",format=yuv420p"
        if effect == "none":
            return base + f",fps={fps}" + finish
        if effect.startswith("slow_zoom"):
            z = f"1+0.08*on/{max(1, frames - 1)}" if effect.endswith("in") else f"1.08-0.08*on/{max(1, frames - 1)}"
            return base + f",zoompan=z='{z}':d=1:s={width}x{height}:fps={fps}" + finish
        direction = effect.removeprefix("pan_")
        factor = f"on/{max(1, frames - 1)}"
        x = f"(iw-iw/zoom)*{factor}" if direction == "right" else f"(iw-iw/zoom)*(1-{factor})" if direction == "left" else "(iw-iw/zoom)/2"
        y = f"(ih-ih/zoom)*{factor}" if direction == "down" else f"(ih-ih/zoom)*(1-{factor})" if direction == "up" else "(ih-ih/zoom)/2"
        return base + f",zoompan=z=1.08:x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}" + finish

    @staticmethod
    def _ass_time(ms: int) -> str:
        cs = round(ms / 10)
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02}:{s:02}.{cs:02}"

    @classmethod
    def caption_line_limit(cls, settings: RenderSettings, requested: int = 24) -> int:
        size = max(18, round(settings.subtitle_font_size * settings.height / 1080))
        margin = max(20, round(settings.width * 0.045))
        # A full-width Japanese glyph can exceed one em in some installed fonts.
        return max(1, min(requested, int((settings.width - 2 * margin) / (size * 1.10))))

    @classmethod
    def _write_ass(cls, project: Project, timeline: Timeline, path: Path, settings: RenderSettings, font: str) -> None:
        style = project.subtitle_style
        size = max(18, round(settings.subtitle_font_size * settings.height / 1080))
        margin = max(20, round(settings.width * 0.045))
        bottom = max(20, round(settings.height * 0.055))
        outline = max(3, round(size * 0.045))
        line_limit = cls.caption_line_limit(settings, style["max_chars_per_line"])
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
            f"Style: Normal,{font},{size},{text_color},{text_color},{outline_color},&H88000000,-1,0,0,0,100,100,0,0,3,{outline},0,2,{margin},{margin},{bottom},1\n"
            f"Style: Headline,{font},{size},{text_color},{text_color},{outline_color},&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,5,{margin},{margin},{bottom},1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )
        by_id = {c.id: c for c in project.subtitle_captions}
        lines = [header]
        for caption in timeline.captions:
            source = by_id.get(caption.id)
            headline = bool(source and source.presentation == "headline" and timeline.headline.get("enabled"))
            def escape(value: str) -> str:
                return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            parts = split_caption_text(caption.text, line_limit, 2)
            if not parts:
                continue
            duration = caption.end_ms - caption.start_ms
            weights = [len(part) for part in parts]
            total = sum(weights)
            boundaries = [caption.start_ms]
            for index in range(1, len(parts)):
                boundaries.append(caption.start_ms + round(duration * sum(weights[:index]) / total))
            boundaries.append(caption.end_ms)
            for part, start, end in zip(parts, boundaries, boundaries[1:]):
                if end <= start:
                    continue
                text = "\\N".join(escape(line) for line in wrap_caption(part, line_limit, 2))
                if not headline:
                    lines.append(f"Dialogue: 0,{cls._ass_time(start)},{cls._ass_time(end)},Normal,,0,0,0,,{text}\n")
                    continue
                dark = round((1 - timeline.headline.get("darkening", 0.7)) * 255)
                text = f"{{\\an5\\bord3\\shad0}}{text}"
                lines.append(f"Dialogue: -1,{cls._ass_time(start)},{cls._ass_time(end)},Headline,,0,0,0,,{{\\an7\\bord0\\shad0\\1c&H000000&\\1a&H{dark:02X}&\\p1}}m 0 0 l {settings.width} 0 {settings.width} {settings.height} 0 {settings.height}{{\\p0}}\n")
                lines.append(f"Dialogue: 0,{cls._ass_time(start)},{cls._ass_time(end)},Headline,,0,0,0,,{{\\alpha&H00&}}{text}\n")
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
        diagnostic_video: Path | None = None
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
                signature = json.dumps(["visual-filter-v2", kind, asdict(event), clip_settings, info["version"],
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
            stage = "audio-mixing"
            emit("mixing audio")
            audio_command = [info["ffmpeg"], "-y"]
            filters = []
            narration_labels = []
            input_index = 0
            cursor_ms = 0
            for block in timeline.blocks:
                if block.start_ms < cursor_ms or block.end_ms <= block.start_ms:
                    raise ValueError(f"Khối lời đọc chồng lấn hoặc không hợp lệ: {block.segment_id}")
                if block.start_ms > cursor_ms:
                    gap = (block.start_ms - cursor_ms) / 1000
                    label = f"gap{len(narration_labels)}"
                    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={gap},asetpts=PTS-STARTPTS[{label}]")
                    narration_labels.append(f"[{label}]")
                audio_command += ["-i", block.audio_path]
                duration = (block.end_ms - block.start_ms) / 1000
                label = f"voice{input_index}"
                # Reset each WAV's PTS and fix its exact timeline length before concat.
                # This avoids FFmpeg mixing dozens of independently delayed inputs.
                filters.append(f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration},"
                    f"asetpts=PTS-STARTPTS[{label}]")
                narration_labels.append(f"[{label}]")
                cursor_ms = block.end_ms
                input_index += 1
            filters.append("".join(narration_labels) +
                           f"concat=n={len(narration_labels)}:v=0:a=1[narration]")
            audio_labels = ["[narration]"]
            for music in timeline.music:
                section = TimelineService.get_section_range(timeline, music.section)
                if not section:
                    continue
                audio_command += ["-stream_loop", "-1", "-i", music.path]
                duration = (section[1] - section[0]) / 1000
                filters.append(f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                               f"atrim=duration={duration},asetpts=PTS-STARTPTS,volume={music.volume},"
                               f"afade=t=in:st=0:d={music.fade_in_ms/1000},"
                               f"afade=t=out:st={max(0,duration-music.fade_out_ms/1000)}:d={music.fade_out_ms/1000},"
                               f"adelay={section[0]}:all=1[m{input_index}]")
                audio_labels.append(f"[m{input_index}]")
                input_index += 1
            if len(audio_labels) == 1:
                filters.append("[narration]anull[aout]")
            else:
                filters.append("".join(audio_labels) +
                               f"amix=inputs={len(audio_labels)}:duration=first:normalize=0[aout]")
            end_seconds = max(end for _, end, _, _ in events) / 1000
            audio_filter = ";".join(filters)
            audio_command += ["-filter_complex", audio_filter, "-map", "[aout]", "-vn",
                              "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                              "-t", str(end_seconds), "-movflags", "+faststart"]
            def file_key(path: Path) -> tuple[str, int, int, str]:
                stat = path.stat()
                return (str(path), stat.st_size, stat.st_mtime_ns, sha256(path.read_bytes()).hexdigest())
            audio_key = sha256(json.dumps(["audio-track-v2", info["version"], audio_filter,
                [(block.start_ms, block.end_ms, file_key(Path(block.audio_path))) for block in timeline.blocks],
                [(music.section.value, music.volume, music.fade_in_ms, music.fade_out_ms,
                  file_key(Path(music.path))) for music in timeline.music]],
                sort_keys=True).encode()).hexdigest()
            audio_file = cache / f"audio-{audio_key}.m4a"
            audio_actual_command: list[str] | None = None
            if not audio_file.is_file():
                audio_temp = cache / f"audio-{audio_key}.partial.m4a"
                try:
                    audio_actual_command = audio_command + [str(audio_temp)]
                    self._run(audio_actual_command, stage, cache)
                    self._verify_packet_timestamps(info["ffprobe"], audio_temp)
                    os.replace(audio_temp, audio_file)
                finally:
                    audio_temp.unlink(missing_ok=True)
            else:
                (cache / "audio-mixing.log").write_text(
                    f"Reused cached AAC narration: {audio_file}\n", encoding="utf-8")

            ass = cache / "captions.ass"
            video_filter = f"fps={settings.fps},format=yuv420p"
            font_path = None
            if timeline.captions:
                emit("burning subtitles")
                font_family, font_path = self._resolve_font(settings)
                self._write_ass(project, timeline, ass, settings, font_family)
                filter_path = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                font_dir = ""
                if font_path:
                    directory_path = str(font_path.parent).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                    font_dir = f":fontsdir='{directory_path}'"
                video_filter += f",ass=filename='{filter_path}'{font_dir}"

            def video_command_for(selected_encoder: str, destination: Path) -> list[str]:
                command = [info["ffmpeg"], "-y", "-i", str(visual), "-an", "-vf", video_filter,
                    "-c:v", selected_encoder]
                if selected_encoder == "libx264":
                    command += ["-preset", settings.preset, "-crf", str(settings.crf)]
                else:
                    command += ["-b:v", "8M" if settings.width >= 1920 else "3M"]
                return command + ["-pix_fmt", "yuv420p", "-r", str(settings.fps),
                    "-t", str(end_seconds), str(destination)]

            def render_video_only(selected_encoder: str) -> tuple[Path, list[str] | None]:
                signature = sha256(json.dumps(["video-burn-v2", info["version"],
                    [file_key(path) for path in used], video_filter, ass.read_text(encoding="utf-8")
                    if timeline.captions else None, file_key(font_path) if font_path else None,
                    selected_encoder,
                    settings.preset, settings.crf, settings.fps], sort_keys=True).encode()).hexdigest()
                destination = cache / f"video-{signature}.mp4"
                command = None
                if not destination.is_file():
                    temporary = cache / f"video-{signature}.partial.mp4"
                    command = video_command_for(selected_encoder, temporary)
                    try:
                        self._run(command, "finalizing", cache)
                        self._verify_packet_timestamps(info["ffprobe"], temporary, "v:0")
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    (cache / "finalizing.log").write_text(
                        f"Reused cached subtitle video: {destination}\n", encoding="utf-8")
                return destination, command

            stage = "finalizing"
            emit("encoding video separately from narration")
            try:
                video_file, video_command = render_video_only(encoder)
            except RenderError:
                if encoder == "libx264" or self._cancel.is_set():
                    raise
                emit(f"{encoder} thất bại khi xuất; thử lại bằng CPU libx264")
                encoder = "libx264"
                video_file, video_command = render_video_only(encoder)

            stage = "muxing"
            emit("muxing video and AAC narration")
            mux_command = [info["ffmpeg"], "-y", "-i", str(video_file), "-i", str(audio_file),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
                "-t", str(end_seconds), "-movflags", "+faststart", str(partial)]
            _write_json(cache / "finalizing-plan.json", {
                "argv": audio_actual_command, "filter_complex": audio_filter,
                "audio_cache_hit": audio_actual_command is None,
                "audio_path": str(audio_file),
                "video_argv": video_command, "video_cache_hit": video_command is None,
                "video_path": str(video_file), "mux_argv": mux_command,
                "audio_blocks": [{"segment_id": block.segment_id, "section": block.section.value,
                    "start_ms": block.start_ms, "end_ms": block.end_ms,
                    "wav_path": block.audio_path} for block in timeline.blocks],
                "intros": [{"section": intro.section.value, "start_ms": intro.start_ms,
                    "end_ms": intro.end_ms, "media_id": intro.media_id} for intro in timeline.intros],
            })
            try:
                self._run(mux_command, stage, cache)
                self._verify_packet_timestamps(info["ffprobe"], partial)
                self._verify_packet_timestamps(info["ffprobe"], partial, "v:0")
            except Exception as exc:
                if partial.is_file():
                    diagnostic_video = self._keep_narration_diagnostic(
                        partial, output_dir, cache, info["ffmpeg"], exc)
                    raise ValueError(f"{exc}; video chẩn đoán: {diagnostic_video}") from exc
                raise
            stage = "verifying-intro-narration"
            emit("verifying intro narration")
            try:
                self._verify_intro_narration(project, timeline, partial, info["ffmpeg"])
            except Exception as exc:
                diagnostic_video = self._keep_narration_diagnostic(
                    partial, output_dir, cache, info["ffmpeg"], exc)
                raise ValueError(f"{exc}; video chẩn đoán: {diagnostic_video}") from exc
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
                "diagnostic_video": str(diagnostic_video) if diagnostic_video else None,
            })
