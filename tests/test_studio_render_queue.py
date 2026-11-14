"""Queue behavior with isolated project snapshots and no live GPT or VOICEVOX calls."""
from copy import deepcopy
from array import array
import json
from pathlib import Path, PureWindowsPath
from queue import Queue
from threading import Event, Lock
import time
from types import SimpleNamespace
import wave

from PIL import Image
import pytest

from te.project.models import SectionName
from te.render.service import RenderError, RenderService
from te.studio.ai_speakers import resolve_japanese_references, segment_passages
from te.studio.multi_voice import confirmed_speaker_data
from te.studio.multi_voice import MultiVoiceService
from te.studio.render_queue import RenderQueue
from te.studio.gui import StudioApp
from te.studio.rendering import StudioRenderService, project_job_lock
from te.studio.service import StudioService
from te.tts import SentenceSpeechService, TTSService


def fixture(tmp_path):
    service = StudioService(tmp_path / "settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    library = tmp_path / "images"
    library.mkdir()
    image = library / "normal.png"
    Image.new("RGB", (320, 180), "blue").save(image)
    def create(name):
        state = service.create_project(name)
        state.narration_mode = "single"  # Most queue fixtures use synthetic single-voice WAVs.
        state.project.tts_settings.provider = "fake"
        state.project.tts_settings.voice_id = "local-test"
        service.set_section(state, SectionName.P1, japanese=f"{name}の話。", vietnamese=f"{name} kể.")
        service.save_project(state)
        service.select_media_folder(state, library)
        return state
    return service, create, image


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Queue did not reach expected state")


def fake_factory(seen, *, entered=None, release=None, fail=None, actual_encoder="h264_nvenc",
                 mutate_asset=None):
    active = {"count": 0, "max": 0}
    guard = Lock()
    def factory(studio):
        stop = Event()
        class Renderer:
            render = SimpleNamespace(cancel_render=stop.set)
            def render_video(self, state, *, preview, progress, _machine_locked):
                with guard:
                    active["count"] += 1
                    active["max"] = max(active["max"], active["count"])
                try:
                    seen.append((state.project.name, state.sections["P1"]["ja"],
                                 studio.settings["render_encoder"], deepcopy(state.speaker_data)))
                    progress("encoding video separately from narration")
                    if entered:
                        entered.set()
                    if release:
                        release.wait(10)
                    if stop.is_set():
                        raise RenderError("finalizing", ["ffmpeg"], "Cancelled")
                    if state.project.name == fail:
                        raise ValueError("synthetic FFmpeg failure")
                    if mutate_asset:
                        mutate_asset.write_bytes(b"modified during render")
                    output = Path(studio.settings["output_root"]) / "final.mp4"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(state.sections["P1"]["ja"].encode("utf-8"))
                    diagnostics = state.directory / "generated/output/diagnostics"
                    diagnostics.mkdir(parents=True, exist_ok=True)
                    (diagnostics / "performance-test-full.json").write_text(
                        json.dumps({"encoder": actual_encoder}),
                        encoding="utf-8")
                    return output
                finally:
                    with guard:
                        active["count"] -= 1
        return Renderer()
    return factory, active


def test_fifo_single_worker_editor_remains_usable_and_outputs_are_unique(tmp_path):
    service, create, _image = fixture(tmp_path)
    kl5, kl6 = create("KL5"), create("KL6")
    seen = []
    entered, release = Event(), Event()
    factory, active = fake_factory(seen, entered=entered, release=release)
    queue = RenderQueue(service, renderer_factory=factory)
    first, second = queue.enqueue(kl5), queue.enqueue(kl6)
    old_script = kl5.sections["P1"]["ja"]
    service.set_section(kl5, SectionName.P1, japanese="修正した話。", vietnamese="KL5 kể.")
    service.save_project(kl5)
    queue.start()
    queue.start()  # Must not create a second worker.
    assert entered.wait(10)
    # This is the same service path that the editor uses while a queue job renders.
    reopened = service.open_project(kl6.directory)
    service.set_section(reopened, SectionName.P1, japanese=reopened.sections["P1"]["ja"],
                        vietnamese="Đang chỉnh KL6.")
    service.save_project(reopened)
    third = queue.enqueue(create("KL7"))
    fourth = queue.enqueue(kl5)
    release.set()
    wait_for(lambda: all(job["status"] == "completed" for job in queue.jobs()))
    jobs = queue.jobs()
    assert [job["id"] for job in jobs] == [first["id"], second["id"], third["id"], fourth["id"]]
    assert [name for name, *_ in seen] == ["KL5", "KL6", "KL7", "KL5"]
    assert seen[0][1] == old_script and seen[-1][1] == kl5.sections["P1"]["ja"]
    assert active["max"] == 1
    assert len({job["output_path"] for job in jobs}) == 4
    assert all(Path(job["output_path"]).is_file() and Path(job["performance_path"]).is_file()
               for job in jobs)
    assert all(job["actual_encoder"] == "h264_nvenc" for job in jobs)
    assert service.open_project(kl5.directory).sections["P1"]["ja"] == kl5.sections["P1"]["ja"]
    assert [job["status"] for job in RenderQueue(StudioService(service.config_path)).jobs()] == [
        "completed"] * 4


def test_full_hd_button_queues_once_and_keeps_other_project_editable(tmp_path):
    service, create, _image = fixture(tmp_path)
    first, second = create("KL5"), create("KL6")
    started, allow_render, allow_snapshot = Event(), Event(), Event()
    factory, _ = fake_factory([], entered=started, release=allow_render)
    queue = RenderQueue(service, renderer_factory=factory)
    actual_enqueue = queue.enqueue
    snapshot_started = Event()
    calls = []
    def delayed_enqueue(state):
        calls.append(state.project.id)
        snapshot_started.set()
        assert allow_snapshot.wait(10)
        return actual_enqueue(state)
    queue.enqueue = delayed_enqueue

    app = StudioApp.__new__(StudioApp)
    app.state = first
    app.queue = queue
    app._queue_events = Queue()
    app._queue_pending = 0
    app._full_render_pending = set()
    app._job_running = False
    app.queue_details = SimpleNamespace(set=lambda _value: None)
    app.render_status = SimpleNamespace(set=lambda _value: None)
    app.queue_tree = SimpleNamespace(exists=lambda _value: False)
    app.root = SimpleNamespace(winfo_exists=lambda: False)
    app._save = lambda: True
    app._refresh_queue = lambda: None
    app._fail = lambda message: pytest.fail(message)
    app._set_job_busy = lambda _busy: pytest.fail("Full HD disabled the editor")
    queue.on_change = lambda: app._queue_events.put(("changed", None))

    app._render_full()
    assert snapshot_started.wait(10)
    app._render_full()  # Repeated click while the snapshot is still being made.
    assert calls == [first.project.id] and not app._job_running
    app.state = second
    app._render_full()  # Another project can be prepared while KL5 is queued.
    allow_snapshot.set()
    wait_for(lambda: len(queue.jobs()) == 2 and started.is_set())
    wait_for(lambda: (app._poll_queue() or not app._full_render_pending))
    assert not app._full_render_pending and not app._job_running
    app.state = first
    app._render_full()  # Existing queued/running job is resumed, not duplicated.
    assert len(queue.jobs()) == 2 and calls.count(first.project.id) == 1
    allow_render.set()
    wait_for(lambda: all(job["status"] == "completed" for job in queue.jobs()))


def test_remove_queued_job_keeps_other_jobs_and_source_project(tmp_path):
    service, create, _image = fixture(tmp_path)
    first, second = create("KL5"), create("KL6")
    queue = RenderQueue(service, renderer_factory=fake_factory([])[0])
    removed = queue.enqueue(first)
    kept = queue.enqueue(second)
    queue.remove_queued(removed["id"])
    assert [job["id"] for job in queue.jobs()] == [kept["id"]]
    assert not Path(removed["snapshot_path"]).exists()
    assert service.open_project(first.directory).project.id == first.project.id


def test_windows_run_path_budget_keeps_existing_snapshot_location(tmp_path):
    service, create, _image = fixture(tmp_path)
    queue = RenderQueue(service, renderer_factory=fake_factory([])[0])
    job = queue.enqueue(create("KL5"))
    assert Path(job["snapshot_path"]).parent == queue.root / "snapshots"
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "completed")
    report = Path(queue.jobs()[0]["performance_path"])
    run = report.parents[3]
    assert run.parent == service.config_path.parent / "w"
    assert run.name.startswith("r-") and len(run.name) <= 12

    # Check the longest FFmpeg cache rename under a representative long
    # Windows profile. PureWindowsPath makes this deterministic on Linux too.
    profile = PureWindowsPath("C:/Users/" + "u" * 99 + "/AppData/Local/TEStudio")
    cache_name = "video-" + "a" * 64 + ".partial.mp4"
    current = profile / "w" / run.name / "generated/cache/render" / cache_name
    previous = profile / "render-queue/runs" / job["id"] / ("b" * 32) / \
        "generated/cache/render" / cache_name
    assert len(str(previous)) > 259
    assert len(str(current)) < 260
    wav_temp = profile / "w" / run.name / "generated/audio/multi_voice" / ("a" * 64 + ".wav")
    mp4_temp = profile / "w" / run.name / "queued-output" / (".te-" + "a" * 32 + ".partial.mp4")
    assert len(str(wav_temp)) < 260
    assert len(str(mp4_temp)) < 260
    assert RenderQueue(StudioService(service.config_path)).jobs()[0]["snapshot_path"] == \
        job["snapshot_path"]


def test_retry_rejoins_fifo_after_jobs_already_waiting(tmp_path):
    service, create, _image = fixture(tmp_path)
    first, second = create("KL5"), create("KL6")
    seen = []
    factory, _ = fake_factory(seen, fail="KL5")
    queue = RenderQueue(service, renderer_factory=factory)
    failed = queue.enqueue(first)
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "failed")
    waiting = queue.enqueue(second)
    queue.retry(failed["id"])
    assert [job["id"] for job in queue.jobs()] == [waiting["id"], failed["id"]]
    wait_for(lambda: all(job["status"] in ("completed", "failed") for job in queue.jobs()))
    assert [name for name, *_ in seen] == ["KL5", "KL6", "KL5"]


def test_changed_asset_fails_one_job_and_retry_uses_frozen_snapshot(tmp_path):
    service, create, image = fixture(tmp_path)
    source = create("KL5")
    seen = []
    factory, _ = fake_factory(seen)
    queue = RenderQueue(service, renderer_factory=factory)
    failed = queue.enqueue(source)
    image.write_bytes(b"modified source image")
    image_before = image.read_bytes()
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "failed")
    assert "Media" in queue.jobs()[0]["error"]
    assert seen == []
    Image.new("RGB", (320, 180), "blue").save(image)
    queue.retry(failed["id"])
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "completed")
    assert image_before != image.read_bytes()
    assert Path(queue.jobs()[0]["snapshot_path"]).is_dir()


def test_new_media_file_requires_project_library_refresh_before_enqueue(tmp_path):
    service, create, image = fixture(tmp_path)
    state = create("KL5")
    Image.new("RGB", (320, 180), "red").save(image.parent / "new.png")
    queue = RenderQueue(service, renderer_factory=fake_factory([])[0])
    with pytest.raises(ValueError, match="tải lại ảnh"):
        queue.enqueue(state)
    assert queue.jobs() == []


def test_asset_changed_during_render_is_not_published(tmp_path):
    service, create, image = fixture(tmp_path)
    state = create("KL5")
    factory, _ = fake_factory([], mutate_asset=image)
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(state)
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "failed")
    assert "Media hoặc font thay đổi trong khi render" in queue.jobs()[0]["error"]
    assert queue.jobs()[0]["output_path"] is None
    assert not list(Path(service.settings["output_root"]).glob("*.mp4"))


def test_recovery_interrupted_retry_and_explicit_update(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    factory, _ = fake_factory([])
    queue = RenderQueue(service, renderer_factory=factory)
    job = queue.enqueue(state)
    queue._set(job["id"], status="running", started_at="2026-01-01T00:00:00+00:00")
    restarted = RenderQueue(StudioService(service.config_path), renderer_factory=factory)
    assert restarted.jobs()[0]["status"] == "interrupted"
    restarted.retry(job["id"])
    assert restarted.jobs()[0]["status"] == "queued"
    service.set_section(state, SectionName.P1, japanese="更新した話。", vietnamese="KL5 kể.")
    service.save_project(state)
    previous = restarted.jobs()[0]["snapshot_path"]
    restarted.update_from_project(job["id"], state)
    updated = restarted.jobs()[0]
    assert updated["snapshot_path"] != previous
    assert updated["configuration"]["render_encoder"] == "auto"
    assert not Path(previous).exists()
    restarted.start()
    wait_for(lambda: restarted.jobs()[0]["status"] == "completed")


def test_machine_encoder_and_output_location_are_frozen_at_enqueue(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    seen = []
    factory, _ = fake_factory(seen)
    queue = RenderQueue(service, renderer_factory=factory)
    original_root = Path(service.settings["output_root"])
    job = queue.enqueue(state)
    service.settings["render_encoder"] = "libx264"
    service.settings["output_root"] = str(tmp_path / "other-output")
    service.save_settings()
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "completed")
    assert seen[0][2] == job["selected_encoder"] == "auto"
    assert Path(queue.jobs()[0]["output_path"]).parent == original_root


def test_subtitle_opacity_is_frozen_until_queue_job_is_explicitly_updated(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    state.project.subtitle_style.update(background_enabled=True, background_opacity=25,
                                        headline_background_opacity=60)
    service.save_project(state)
    queue = RenderQueue(service, renderer_factory=fake_factory([])[0])
    job = queue.enqueue(state)
    state.project.subtitle_style.update(background_enabled=False, background_opacity=100,
                                        headline_background_opacity=0)
    service.save_project(state)
    frozen = service.open_project(Path(job["snapshot_path"]))
    assert frozen.project.subtitle_style["background_opacity"] == 25
    assert frozen.project.subtitle_style["headline_background_opacity"] == 60
    assert frozen.project.subtitle_style["background_enabled"] is True
    queue.update_from_project(job["id"], state)
    refreshed = service.open_project(Path(queue.jobs()[0]["snapshot_path"]))
    assert refreshed.project.subtitle_style["background_opacity"] == 100
    assert refreshed.project.subtitle_style["headline_background_opacity"] == 0
    assert refreshed.project.subtitle_style["background_enabled"] is False


def test_cancel_current_continues_next_job_and_pause_keeps_queued(tmp_path):
    service, create, _image = fixture(tmp_path)
    first, second = create("KL5"), create("KL6")
    seen = []
    entered, release = Event(), Event()
    factory, _ = fake_factory(seen, entered=entered, release=release)
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(first)
    queue.enqueue(second)
    queue.start()
    assert entered.wait(10)
    queue.cancel_current()
    release.set()
    wait_for(lambda: [job["status"] for job in queue.jobs()] == ["cancelled", "completed"])
    assert queue.jobs()[0]["output_path"] is None
    assert len(seen) == 2
    third = queue.enqueue(create("KL7"))
    queue.pause_after_current()
    assert next(job for job in queue.jobs() if job["id"] == third["id"])["status"] == "queued"
    queue.start()
    wait_for(lambda: next(job for job in queue.jobs() if job["id"] == third["id"])["status"] == "completed")


def test_pause_after_current_and_other_instance_cannot_start_worker(tmp_path):
    service, create, _image = fixture(tmp_path)
    seen = []
    entered, release = Event(), Event()
    factory, _ = fake_factory(seen, entered=entered, release=release)
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(create("KL5"))
    queue.enqueue(create("KL6"))
    queue.start()
    assert entered.wait(10)
    other = RenderQueue(StudioService(service.config_path), renderer_factory=factory)
    with pytest.raises(RuntimeError, match="cửa sổ TE Studio khác"):
        other.start()
    queue.pause_after_current()
    release.set()
    wait_for(lambda: [job["status"] for job in queue.jobs()] == ["completed", "queued"])
    wait_for(lambda: not queue.is_active())
    other.start()
    wait_for(lambda: [job["status"] for job in other.jobs()] == ["completed", "completed"])


def test_failed_job_isolation_snapshot_wav_cache_and_machine_encoder(tmp_path):
    service, create, _image = fixture(tmp_path)
    failing, good = create("Broken"), create("KL6")
    StudioRenderService(service).generate_narration(good)
    service.settings["render_encoder"] = "h264_nvenc"
    service.save_settings()
    seen = []
    factory, _ = fake_factory(seen, fail="Broken", actual_encoder="libx264")
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(failing)
    queued = queue.enqueue(good)
    snapshot = service.open_project(Path(queued["snapshot_path"]))
    assert snapshot.project.sentence_audio_records
    assert all(Path(record.path).is_file() and record.path.startswith(str(snapshot.directory))
               for record in snapshot.project.sentence_audio_records)
    queue.start()
    wait_for(lambda: [job["status"] for job in queue.jobs()] == ["failed", "completed"])
    assert "synthetic FFmpeg failure" in queue.jobs()[0]["error"]
    assert seen[1][2] == "h264_nvenc"
    assert queue.jobs()[1]["selected_encoder"] == "h264_nvenc"
    assert queue.jobs()[1]["actual_encoder"] == "libx264"  # Hardware fallback is recorded.


def test_snapshot_audio_wav_tampering_is_rejected_before_execution(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    StudioRenderService(service).generate_narration(state)
    queue = RenderQueue(service, renderer_factory=fake_factory([])[0])
    job = queue.enqueue(state)
    snapshot = service.open_project(Path(job["snapshot_path"]))
    Path(snapshot.project.sentence_audio_records[0].path).write_bytes(b"damaged")
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] == "failed")
    assert "Snapshot đã thay đổi" in queue.jobs()[0]["error"]


def test_multi_voice_attribution_survives_queue_snapshot_and_manual_lock(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    state.project.tts_settings.provider = "voicevox"
    state.project.tts_settings.voice_id = "A"
    state.project.tts_settings.style = "ノーマル"
    passages = [item for item in segment_passages(state.sections).values() if item["language"] == "ja"]
    rows, _ = resolve_japanese_references([{"japanese_id": item["id"], "role": "A",
        "confidence": "high", "warning": ""} for item in passages], state.sections)
    state.speaker_data = confirmed_speaker_data(state, rows, state.speaker_data["identities"])
    state.speaker_data["voices"]["A"] = {"speaker_uuid": "A", "style_id": 1,
                                          "speaker": "A", "style": "ノーマル"}
    state.narration_mode = "multi"
    service.save_project(state)
    seen = []
    factory, _ = fake_factory(seen)
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(state)
    machine = service.config_path.parent / "render-execution"
    with project_job_lock(machine):
        with pytest.raises(ValueError, match="đang tạo lời đọc hoặc render"):
            StudioRenderService(service).render_video(state, preview=False)
        queue.start()
        wait_for(lambda: queue.jobs()[0]["stage"] == "Đang chờ render khác trên máy này")
        assert queue.jobs()[0]["status"] == "running"
    wait_for(lambda: queue.jobs()[0]["status"] == "completed")
    assert seen[0][3]["status"] == "confirmed"
    assert seen[0][3]["voices"]["A"]["style_id"] == 1


def test_real_queue_full_hd_preserves_cached_wav_and_performance_report(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    state.motion_preset = "none"
    service.save_project(state)
    StudioRenderService(service).generate_narration(state)
    service.settings["render_encoder"] = "libx264"
    service.save_settings()
    queue = RenderQueue(service)
    queue.enqueue(state)
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] in ("completed", "failed"), timeout=90)
    job = queue.jobs()[0]
    assert job["status"] == "completed", job["error"]
    assert Path(job["output_path"]).is_file() and Path(job["performance_path"]).is_file()
    report = json.loads(Path(job["performance_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "success" and report["counts"]["new_wavs"] == 0
    assert report["resolution"] == [1920, 1080] and report["encoder"] == "libx264"
    assert job["actual_encoder"] == "libx264"


def test_real_multi_voice_queue_uses_japanese_first_assignment(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    service.set_section(state, SectionName.P1, japanese="司会「発言」終。", vietnamese="MC và nhân vật.")
    state.project.tts_settings.provider = "voicevox"
    state.project.tts_settings.voice_id = "A"
    state.project.tts_settings.style = "ノーマル"
    state.project.tts_settings.sample_rate = 24000
    state.motion_preset = "none"
    service.save_project(state)
    passages = [item for item in segment_passages(state.sections).values() if item["language"] == "ja"]
    rows, _ = resolve_japanese_references([{"japanese_id": item["id"], "role": role,
        "confidence": "high", "warning": ""} for item, role in zip(passages, ("A", "B", "A"))], state.sections)
    state.speaker_data["voices"] = {role: {"speaker_uuid": role, "style_id": index,
        "speaker": role, "style": "ノーマル"} for index, role in enumerate(("A", "B"), 1)}
    state.narration_mode = "multi"
    service.save_project(state)
    service.confirm_speaker_assignments(state, rows, state.speaker_data["identities"],
                                        deepcopy(state.sections))
    service.settings["render_encoder"] = "libx264"
    service.save_settings()

    class FakeVoicevox:
        calls = []
        def check_connection(self, _settings):
            return "fake-engine-1"
        def list_voices(self, _settings):
            return [{"name": role, "speaker_uuid": role,
                     "styles": [{"id": index, "name": "ノーマル"}]}
                    for index, role in enumerate(("A", "B"), 1)]
        def synthesize(self, text, settings, output, *, style_id=None):
            self.calls.append((text, settings.voice_id, style_id))
            with wave.open(str(output), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(settings.sample_rate)
                stream.writeframes(array("h", [1000] * (2400 * len(text))).tobytes())
            return {"speaker_uuid": settings.voice_id, "style_id": style_id}
    fake = FakeVoicevox()
    def renderer(local):
        return StudioRenderService(local,
            speech=SentenceSpeechService(TTSService(providers={"voicevox": fake})),
            multi_voice=MultiVoiceService(fake))
    queue = RenderQueue(service, renderer_factory=renderer)
    queue.enqueue(state)
    queue.start()
    wait_for(lambda: queue.jobs()[0]["status"] in ("completed", "failed"), timeout=90)
    job = queue.jobs()[0]
    assert job["status"] == "completed", job["error"]
    assert [role for _, role, _ in fake.calls] == ["A", "B", "A"]
    assert "".join(text for text, _, _ in fake.calls) == state.sections["P1"]["ja"]
    assert Path(job["output_path"]).is_file()


def test_cancel_terminates_active_ffmpeg_without_publishing(tmp_path):
    service, create, _image = fixture(tmp_path)
    state = create("KL5")
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    active = []
    def factory(_local):
        class SlowRenderer:
            def __init__(self):
                self.render = RenderService()
            def render_video(self, state, *, preview, progress, _machine_locked):
                folder = state.directory / "generated/cache/render"
                folder.mkdir(parents=True, exist_ok=True)
                output = folder / "slow.mp4"
                progress("FFmpeg video encoding")
                self.render._run([ffmpeg, "-y", "-re", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x180:r=30:d=10", "-an", "-c:v", "libx264", str(output)],
                    "finalizing", folder)
                return output
        instance = SlowRenderer()
        active.append(instance)
        return instance
    queue = RenderQueue(service, renderer_factory=factory)
    queue.enqueue(state)
    queue.start()
    wait_for(lambda: active and active[0].render._process is not None)
    process = active[0].render._process
    queue.cancel_current()
    wait_for(lambda: queue.jobs()[0]["status"] == "cancelled")
    assert process.poll() is not None
    assert queue.jobs()[0]["output_path"] is None
    assert not list(Path(service.settings["output_root"]).glob("*.mp4"))
