"""Low-overhead wall-clock timings for one Studio render attempt."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
from time import perf_counter
from typing import Callable

from te.project.manager import _write_json


STAGES = ("voicevox_setup", "narration_synthesis", "wav_cache_lookup", "asset_timeline_preparation",
          "subtitle_preparation", "ffmpeg_video_encoding", "audio_composition_muxing",
          "final_verification")


def hardware_info() -> dict:
    cpu = platform.processor() or platform.machine() or "unknown"
    gpu = None
    if os.name != "nt":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        try:
            for card in Path("/sys/class/drm").glob("card[0-9]*"):
                vendor = card / "device" / "vendor"
                device = card / "device" / "device"
                if vendor.is_file() and device.is_file():
                    gpu = f"PCI {vendor.read_text().strip()}:{device.read_text().strip()}"
                    break
        except OSError:
            pass
    return {"cpu": cpu, "cpu_count": os.cpu_count(), "gpu": gpu}


class PerformanceReport:
    """Stage intervals are additive within a stage; stage totals must not be summed."""

    def __init__(self, path: Path, project_id: str, *, preview: bool,
                 narration_mode: str, clock: Callable[[], float] = perf_counter):
        self.path = Path(path)
        self.clock = clock
        self.started = clock()
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.project_id = project_id
        self.preview = preview
        self.narration_mode = narration_mode
        self.stages = {name: {"duration_seconds": 0.0, "status": "not_started", "invocations": 0}
                       for name in STAGES}
        self.active: dict[str, float] = {}
        self.counts = {"wav_cache_hits": 0, "new_wavs": 0}
        self._cache_hit_paths: set[str] = set()
        self.video_duration_seconds: float | None = None
        self.resolution: list[int] | None = None
        self.encoder: str | None = None
        self.status = "running"
        self.error_type: str | None = None
        self.hardware = hardware_info()

    def start(self, stage: str) -> None:
        if stage not in self.stages or stage in self.active:
            raise ValueError(f"Invalid or active performance stage: {stage}")
        self.active[stage] = self.clock()
        self.stages[stage]["status"] = "running"
        self.stages[stage]["invocations"] += 1

    def stop(self, stage: str, *, failed: bool = False) -> None:
        beginning = self.active.pop(stage)
        self.stages[stage]["duration_seconds"] += max(0.0, self.clock() - beginning)
        self.stages[stage]["status"] = "failed" if failed else "completed"

    @contextmanager
    def measure(self, stage: str):
        self.start(stage)
        try:
            yield
        except BaseException:
            self.stop(stage, failed=True)
            raise
        else:
            self.stop(stage)

    def increment(self, key: str, count: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + count

    def cache_hit(self, path: Path | str) -> None:
        identifier = str(path)
        if identifier not in self._cache_hit_paths:
            self._cache_hit_paths.add(identifier)
            self.increment("wav_cache_hits")

    def finish(self, status: str, error: Exception | None = None) -> dict:
        now = self.clock()
        for stage, beginning in tuple(self.active.items()):
            self.stages[stage]["duration_seconds"] += max(0.0, now - beginning)
            self.stages[stage]["status"] = "incomplete"
            del self.active[stage]
        self.status = status
        self.error_type = type(error).__name__ if error else None
        payload = {
            "timestamp": self.timestamp, "project_id": self.project_id,
            "render_type": "preview" if self.preview else "full_hd",
            "video_duration_seconds": self.video_duration_seconds,
            "resolution": self.resolution, "narration_mode": self.narration_mode,
            "video_codec": "h264", "encoder": self.encoder, "hardware": self.hardware,
            "status": self.status, "error_type": self.error_type,
            "stages": self.stages, "counts": self.counts,
            "total_seconds": max(0.0, now - self.started),
            "timing_note": "Stage intervals may be non-contiguous or overlap; do not sum them to obtain total time.",
        }
        _write_json(self.path, payload)
        return payload
