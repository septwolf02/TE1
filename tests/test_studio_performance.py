"""Deterministic timing reports plus a small FFmpeg render and mocked failure."""
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from te.project.models import SectionName
from te.render.service import RenderError
from te.studio.performance import PerformanceReport
from te.studio.rendering import StudioRenderService
from te.studio.service import StudioService


def test_report_keeps_overlapping_intervals_and_incomplete_stage(tmp_path):
    times = iter((0, 1, 2, 8, 9, 10))
    report = PerformanceReport(tmp_path / "performance.json", "project-1",
                               preview=True, narration_mode="multi", clock=lambda: next(times))
    report.start("narration_synthesis")
    report.start("wav_cache_lookup")
    report.stop("narration_synthesis")
    report.stop("wav_cache_lookup")
    payload = report.finish("success")
    assert payload["total_seconds"] == 10
    assert payload["stages"]["narration_synthesis"]["duration_seconds"] == 7
    assert payload["stages"]["wav_cache_lookup"]["duration_seconds"] == 7
    assert payload["stages"]["ffmpeg_video_encoding"]["status"] == "not_started"
    assert json.loads(report.path.read_text(encoding="utf-8")) == payload

    times = iter((0, 1, 5))
    failed = PerformanceReport(tmp_path / "failed.json", "project-1", preview=False,
                               narration_mode="single", clock=lambda: next(times))
    failed.start("ffmpeg_video_encoding")
    result = failed.finish("cancelled", RuntimeError("cancel"))
    assert result["stages"]["ffmpeg_video_encoding"] == {
        "duration_seconds": 4, "status": "incomplete", "invocations": 1}
    assert result["status"] == "cancelled" and result["error_type"] == "RuntimeError"


def fixture(tmp_path):
    studio = StudioService(tmp_path / "settings.json")
    studio.set_directories(tmp_path / "projects", tmp_path / "output")
    state = studio.create_project("Performance fixture")
    state.narration_mode = "single"  # This fixture exercises the legacy single-voice renderer.
    state.project.tts_settings.provider = "fake"
    state.project.tts_settings.voice_id = "local-test"
    studio.set_section(state, SectionName.P1, japanese="試合開始。", vietnamese="Bắt đầu.")
    studio.save_project(state)
    folder = tmp_path / "media"
    folder.mkdir()
    Image.new("RGB", (320, 180), (20, 80, 140)).save(folder / "normal.png")
    studio.select_media_folder(state, folder)
    return studio, state


def reports(state):
    return sorted((state.directory / "generated/output/diagnostics").glob("performance-*.json"))


def test_render_report_tracks_cache_and_ffmpeg_stages(tmp_path):
    studio, state = fixture(tmp_path)
    workflow = StudioRenderService(studio)
    output = workflow.render_video(state, preview=True)
    assert output.is_file()
    first = json.loads(reports(state)[-1].read_text(encoding="utf-8"))
    assert first["status"] == "success"
    assert first["project_id"] == state.project.id
    assert first["render_type"] == "preview"
    assert first["resolution"] == [960, 540]
    assert first["narration_mode"] == "single"
    assert first["video_duration_seconds"] > 0
    assert first["encoder"] == "libx264"
    assert "cpu" in first["hardware"] and "gpu" in first["hardware"]
    assert first["counts"]["new_wavs"] == 1
    assert all(first["stages"][stage]["status"] == "completed" for stage in (
        "narration_synthesis", "wav_cache_lookup", "asset_timeline_preparation",
        "subtitle_preparation", "ffmpeg_video_encoding", "audio_composition_muxing",
        "final_verification"))
    assert first["total_seconds"] > 0
    from te.studio.gui import StudioApp
    summary = SimpleNamespace(value="", set=lambda value: setattr(summary, "value", value))
    app = StudioApp.__new__(StudioApp)
    app.state = state
    app.performance_summary = summary
    app._refresh_performance_summary()
    assert "Thời gian dựng: Preview" in summary.value and "WAV mới 1" in summary.value
    workflow.render_video(state, preview=True)
    assert len(reports(state)) == 2
    second = json.loads(reports(state)[-1].read_text(encoding="utf-8"))
    assert second["counts"]["new_wavs"] == 0
    assert second["counts"]["wav_cache_hits"] >= 1
    assert second["stages"]["narration_synthesis"]["status"] == "not_started"


def test_mocked_ffmpeg_failure_keeps_partial_report(tmp_path, monkeypatch):
    studio, state = fixture(tmp_path)
    workflow = StudioRenderService(studio)

    def failed_subprocess(_command, stage, _cache):
        raise RenderError(stage, ["ffmpeg"], "mocked failure")

    monkeypatch.setattr(workflow.render, "_run", failed_subprocess)
    with pytest.raises(RenderError, match="mocked failure"):
        workflow.render_video(state, preview=True)
    data = json.loads(reports(state)[-1].read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["error_type"] == "RenderError"
    assert data["stages"]["ffmpeg_video_encoding"]["status"] == "incomplete"
    assert data["stages"]["final_verification"]["status"] == "not_started"
    assert data["counts"]["new_wavs"] == 1


def test_cancelled_render_report(tmp_path, monkeypatch):
    studio, state = fixture(tmp_path)
    workflow = StudioRenderService(studio)

    def cancelled_subprocess(_command, stage, _cache):
        workflow.render.cancel_render()
        raise RenderError(stage, ["ffmpeg"], "cancelled")

    monkeypatch.setattr(workflow.render, "_run", cancelled_subprocess)
    with pytest.raises(RenderError):
        workflow.render_video(state, preview=True)
    data = json.loads(reports(state)[-1].read_text(encoding="utf-8"))
    assert data["status"] == "cancelled"
