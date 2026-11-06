"""Phase 2 application workflow with real FFmpeg and deterministic local WAVs."""
from pathlib import Path
import json
import os
import subprocess

from PIL import Image
import pytest

from te.project import ProjectManager, SectionName
from te.media import KeyPlacementService, MediaLibraryService
from te.tts import SentenceSpeechService
from te.render import RenderService
from te.render.service import RenderError
from te.studio.service import StudioService
from te.studio.rendering import StudioRenderService, project_job_lock
from te.timeline import TimelineService


def studio_project(tmp_path, *, intro=True, key=True):
    studio = StudioService(tmp_path / "settings.json")
    studio.set_directories(tmp_path / "projects", tmp_path / "output")
    state = studio.create_project("日本の試合: Test")
    state.project.tts_settings.provider = "fake"
    state.project.tts_settings.voice_id = "local-test"
    studio.set_section(state, SectionName.P1,
        japanese="試合が始まる。秘密が明かされる！観客が沸いた。",
        vietnamese="Trận đấu bắt đầu. Bí mật được tiết lộ! Khán giả reo hò.")
    studio.set_section(state, SectionName.P2,
        japanese="物語は続く。", vietnamese="Câu chuyện tiếp tục.")
    studio.save_project(state)
    library = tmp_path / "media"
    library.mkdir()
    for index in range(2):
        Image.new("RGB", (320, 180), (20 + index * 80, 100, 160)).save(library / f"normal{index}.png")
    studio.select_media_folder(state, library)
    if intro:
        intro_path = tmp_path / "intro.mp4"
        ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "color=c=red:s=320x180:r=30:d=0.5", "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(intro_path)], capture_output=True, check=True)
        studio.set_intro(state, SectionName.P1, intro_path)
    if key:
        key_path = tmp_path / "key.png"
        Image.new("RGB", (320, 180), (255, 30, 180)).save(key_path)
        draft = studio.add_key_draft(state, key_path)
        vi = studio.vietnamese_units(state, SectionName.P1)[1]
        ja = studio.japanese_choices(state, SectionName.P1)[1]
        studio.confirm_key_draft(state, draft["id"], section=SectionName.P1,
            vietnamese_key=vi.key, sentence_id=ja.sentence_id, duration_seconds=1.7)
    return studio, state


def test_studio_fake_tts_timeline_intro_key_and_real_preview(tmp_path):
    studio, state = studio_project(tmp_path)
    state.transition = "crossfade"
    studio.save_project(state)
    workflow = StudioRenderService(studio)
    with pytest.raises(ValueError, match="cập nhật lời đọc"):
        workflow.validate_inputs(state)
    messages = []
    assert workflow.generate_narration(state, messages.append) == 2
    assert workflow.generate_narration(state, messages.append) == 0
    # Older Phase 1 projects can have a selected external intro without probe metadata.
    intro_id = state.project.section_intros["P1"]
    next(item for item in state.project.media if item.id == intro_id).duration_seconds = None
    studio.save_project(state)
    timeline = workflow.prepare_timeline(state)
    assert timeline.intros[0].start_ms == timeline.blocks[0].start_ms == 0
    assert timeline.transitions[0].preset == "crossfade"
    assert timeline.blocks[0].effect in {"slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right"}
    assert timeline.captions[0].start_ms == 0
    key = state.project.key_placements[0]
    key_visual = next(item for item in timeline.placements if item.id == key.id)
    spoken = TimelineService.get_sentence_range(state.project, timeline, key.sentence_id)
    assert key_visual.start_ms == spoken[0]
    assert key_visual.end_ms > spoken[1]
    assert key_visual.start_ms >= timeline.intros[0].end_ms
    events = RenderService._visual_events(state.project, timeline)
    assert events[0][0] == 0 and all(left[1] == right[0] for left, right in zip(events, events[1:]))
    assert all(kind == "intro" or event.media_id is not None for _, _, kind, event in events)
    ordinary = {item.id for item in state.project.media if item.category.value == "normal"}
    visual_order = [item.media_id for item in timeline.placements if item.media_id in ordinary]
    assert all(first != second for first, second in zip(visual_order, visual_order[1:]))
    output = workflow.render_video(state, preview=True, progress=messages.append)
    assert output.is_file()
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    def center_rgb(seconds):
        return subprocess.run([ffmpeg, "-v", "error", "-ss", str(seconds), "-i", str(output),
            "-frames:v", "1", "-vf", "crop=64:64:448:238,scale=1:1", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "-"], capture_output=True, check=True).stdout[:3]
    before = center_rgb((key_visual.start_ms - 150) / 1000)
    after = center_rgb((key_visual.start_ms + 150) / 1000)
    assert after[1] < before[1] - 30 and after[2] < before[2] - 60
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    video = next(item for item in probe["streams"] if item["codec_type"] == "video")
    audio = next(item for item in probe["streams"] if item["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"]) == (960, 540, "h264")
    assert audio["codec_name"] == "aac"
    assert any("rendering segment" in item for item in messages)
    assert studio.open_project(state.directory).project.key_placements[0].sentence_id == key.sentence_id


def test_missing_media_intro_and_stale_audio_block_render(tmp_path):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    state.sections["P1"]["ja"] += "次の一球。"
    studio.save_project(state)
    with pytest.raises(ValueError, match="cập nhật lời đọc"):
        workflow.prepare_timeline(state)
    workflow.generate_narration(state)
    Path(state.project.media_library_path, "normal0.png").unlink()
    Path(state.project.media_library_path, "normal1.png").unlink()
    with pytest.raises(ValueError, match="không có ảnh"):
        workflow.prepare_timeline(state)


def test_missing_random_image_is_reassigned_without_black_gap(tmp_path):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    workflow.prepare_timeline(state)
    missing_id = state.project.media_assignments[0].media_id
    missing_path = Path(next(item for item in state.project.media if item.id == missing_id).path)
    missing_path.unlink()
    with pytest.warns(RuntimeWarning, match="media source"):
        timeline = workflow.prepare_timeline(state)
    assert all(item.media_id != missing_id for item in state.project.media_assignments)
    assert all(kind == "intro" or event.media_id is not None
               for _, _, kind, event in RenderService._visual_events(state.project, timeline))


def test_final_name_versioning_and_reopen_phase1(tmp_path):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    reopened = studio.open_project(state.directory)
    workflow.prepare_timeline(reopened)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"finished mp4 fixture")
    first = workflow._publish(source, tmp_path / "output", "日本: 試合?")
    second = workflow._publish(source, tmp_path / "output", "日本: 試合?")
    assert first.name == "日本_ 試合_.mp4"
    assert second.name == "日本_ 試合__v2.mp4"
    assert first.read_bytes() == second.read_bytes()
    unicode_name = "日本" * 30
    assert workflow._publish(source, tmp_path / "output", unicode_name).name == unicode_name + ".mp4"
    assert workflow._publish(source, tmp_path / "output", "日本" * 150).is_file()


def test_encoder_probe_cpu_fallback(monkeypatch):
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    encoder, _ = RenderService.choose_encoder(ffmpeg)
    assert encoder in {"libx264", "h264_nvenc", "h264_qsv", "h264_amf"}
    original = subprocess.run
    def listed_but_broken(command, *args, **kwargs):
        if "-encoders" in command:
            return subprocess.CompletedProcess(command, 0, stdout=" V..... h264_nvenc\n")
        if "h264_nvenc" in command:
            return subprocess.CompletedProcess(command, 1, stderr="device unavailable")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", listed_but_broken)
    assert RenderService.choose_encoder(ffmpeg)[0] == "libx264"


def test_same_project_job_lock(tmp_path):
    with project_job_lock(tmp_path):
        with pytest.raises(ValueError, match="đang tạo lời đọc hoặc render"):
            with project_job_lock(tmp_path):
                pass
    with project_job_lock(tmp_path):
        pass


def test_intro_key_conflict_is_actionable(tmp_path):
    studio, state = studio_project(tmp_path)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    intro_id = state.project.section_intros["P1"]
    intro = next(item for item in state.project.media if item.id == intro_id)
    ffmpeg = RenderService.check_ffmpeg()["ffmpeg"]
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=red:s=320x180:r=30:d=2", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", intro.path], capture_output=True, check=True)
    with pytest.raises(ValueError, match="trùng thời gian intro"):
        workflow.prepare_timeline(state)


def test_multiple_key_placements_and_overlap_warning(tmp_path):
    studio, state = studio_project(tmp_path)
    second_path = tmp_path / "second-key.png"
    Image.new("RGB", (320, 180), (250, 200, 30)).save(second_path)
    draft = studio.add_key_draft(state, second_path)
    vi = studio.vietnamese_units(state, SectionName.P1)[2]
    ja = studio.japanese_choices(state, SectionName.P1)[2]
    second = studio.confirm_key_draft(state, draft["id"], section=SectionName.P1,
        vietnamese_key=vi.key, sentence_id=ja.sentence_id, duration_seconds=0.4)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    with pytest.raises(ValueError, match="KEY image placements overlap"):
        workflow.prepare_timeline(state)
    first = state.project.key_placements[0]
    first.duration_ms = 500
    studio.save_project(state)
    timeline = workflow.prepare_timeline(state)
    assert {item.id for item in timeline.placements if item.id in {first.id, second.id}} == {first.id, second.id}


def test_missing_intro_is_actionable(tmp_path):
    studio, state = studio_project(tmp_path, intro=True, key=False)
    workflow = StudioRenderService(studio)
    workflow.generate_narration(state)
    intro_id = state.project.section_intros["P1"]
    Path(next(item for item in state.project.media if item.id == intro_id).path).unlink()
    with pytest.raises(ValueError, match="Intro P1 bị thiếu"):
        workflow.prepare_timeline(state)


def test_shuffle_bag_never_repeats_with_two_images():
    from te.media.key_service import OrdinaryImageBag
    first = OrdinaryImageBag(["a", "b"], 41)
    second = OrdinaryImageBag(["a", "b"], 41)
    sequence = [first.draw() for _ in range(20)]
    assert sequence == [second.draw() for _ in range(20)]
    assert all(left != right for left, right in zip(sequence, sequence[1:]))


def test_key_duration_can_cross_segment_within_same_section(tmp_path):
    directory = tmp_path / "legacy-project"
    manager = ProjectManager()
    project = manager.create(directory, "Cross-segment KEY")
    first = manager.add_segment(project, SectionName.P1, 1, "最初。", "Đầu tiên.")
    manager.add_segment(project, SectionName.P1, 2, "次です。", "Tiếp theo.")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    for name, color in (("a.png", "blue"), ("b.png", "green"), ("key.png", "red")):
        Image.new("RGB", (320, 180), color).save(media_dir / name)
    MediaLibraryService().scan_library(project, media_dir)
    media = {item.filename: item for item in project.media}
    MediaLibraryService().mark_key(project, media["key.png"].id)
    manager.save(directory, project)
    SentenceSpeechService().sync_all(project, directory)
    target = next(item for item in project.sentence_units if item.segment_id == first.id)
    KeyPlacementService.confirm_mapping(project, directory, target.id, "Đầu tiên.")
    key = KeyPlacementService.assign_key(project, directory, media["key.png"].id,
        target.id, duration_ms=1500)
    KeyPlacementService.assign_random_images(project, directory)
    timeline, _ = TimelineService().build_timeline(project, directory)
    reveal = next(item for item in timeline.placements if item.id == key.id)
    assert reveal.start_ms == 0 and reveal.end_ms == 1500
    assert reveal.end_ms > timeline.blocks[0].end_ms
    events = RenderService._visual_events(project, timeline)
    assert all(left[1] == right[0] for left, right in zip(events, events[1:]))


def test_real_full_hd_export_cpu_fallback_and_saved_output_path(tmp_path, monkeypatch):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    state.motion_preset = "none"
    studio.save_project(state)
    workflow = StudioRenderService(studio)
    assert not state.project.sentence_audio_records
    original_run = RenderService._run
    monkeypatch.setattr(RenderService, "choose_encoder",
        staticmethod(lambda _ffmpeg: ("h264_nvenc", ())))
    def hardware_fails(self, command, stage, cache):
        if stage == "finalizing" and "h264_nvenc" in command:
            raise RenderError(stage, command, "test hardware failure")
        return original_run(self, command, stage, cache)
    monkeypatch.setattr(RenderService, "_run", hardware_fails)
    result = workflow.render_video(state, preview=False)
    assert result.is_file() and result.parent == Path(studio.settings["output_root"])
    assert all(record.status == "ready" for record in state.project.sentence_audio_records)
    assert result.name == "日本の試合_ Test.mp4"
    assert studio.open_project(state.directory).last_final == str(result)
    ffprobe = RenderService.check_ffmpeg()["ffprobe"]
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(result)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    video = next(item for item in probe["streams"] if item["codec_type"] == "video")
    assert (video["width"], video["height"], video["codec_name"]) == (1920, 1080, "h264")
    manifest = json.loads((state.directory / "generated/output/final.manifest.json").read_text(encoding="utf-8"))
    assert manifest["encoder_used"] == "libx264"


def test_one_click_preview_reflows_200_character_sentence(tmp_path):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    long_text = "「監督が語り、最後の一球で流れが変わった」と振り返り、" * 8 + "試合は終わった。"
    studio.set_section(state, SectionName.P1, japanese=long_text, vietnamese="Câu chuyện dài.")
    state.motion_preset = "none"
    studio.save_project(state)
    assert not state.project.sentence_audio_records
    workflow = StudioRenderService(studio)
    output = workflow.render_video(state, preview=True)
    assert output.is_file() and state.project.sentence_audio_records
    first_segment = next(item for item in state.project.segments if item.section == SectionName.P1)
    captions = [item for item in state.project.subtitle_captions if item.segment_id == first_segment.id]
    assert len(captions) > 4
    assert "".join(item.text for item in captions) == long_text
    assert all(len(item.text) <= 48 for item in captions)


def test_retry_after_subtitle_burn_failure_reuses_wav_and_clips(tmp_path, monkeypatch):
    studio, state = studio_project(tmp_path, intro=False, key=False)
    workflow = StudioRenderService(studio)
    def fail_burn(*_args, **_kwargs):
        raise ValueError("simulated subtitle burn failure")
    with monkeypatch.context() as patch:
        patch.setattr(workflow.render, "_write_ass", fail_burn)
        with pytest.raises(ValueError, match="subtitle burn failure"):
            workflow.render_video(state, preview=True)
    wavs = list((state.directory / "generated/audio/sentences").glob("*.wav"))
    clips = [item for item in (state.directory / "generated/cache/render").glob("*.mp4")
             if item.name != "visual.mp4"]
    assert wavs and clips
    wav_times = {item: item.stat().st_mtime_ns for item in wavs}
    clip_times = {item: item.stat().st_mtime_ns for item in clips}
    generations = {item.sentence_id: item.generation_version for item in state.project.sentence_audio_records}
    output = workflow.render_video(state, preview=True)
    assert output.is_file()
    assert {item: item.stat().st_mtime_ns for item in wavs} == wav_times
    assert {item: item.stat().st_mtime_ns for item in clips} == clip_times
    assert {item.sentence_id: item.generation_version for item in state.project.sentence_audio_records} == generations


@pytest.mark.skipif(os.environ.get("TE_STUDIO_GUI_SMOKE") != "1", reason="requires a desktop display")
def test_tk_buttons_generate_and_render_real_preview(tmp_path, monkeypatch):
    import time
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    from te.studio.gui import StudioApp

    studio, state = studio_project(tmp_path, intro=False, key=False)
    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "Tạm")
    monkeypatch.setattr(messagebox, "askyesno", lambda *args, **kwargs: False)
    root = tk.Tk()
    app = StudioApp(root, studio)
    app._load_state(state)
    errors = []
    monkeypatch.setattr(app, "_fail", errors.append)
    def button(label):
        pending = [app.body]
        while pending:
            widget = pending.pop()
            if isinstance(widget, ttk.Button) and widget.cget("text") == label:
                return widget
            pending.extend(widget.winfo_children())
        raise AssertionError(label)
    def wait_done():
        deadline = time.monotonic() + 30
        while app._job_running and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not app._job_running and not errors
    button("Xuất Full HD 1920×1080").invoke()
    wait_done()
    assert state.project.sentence_audio_records
    assert state.last_final and Path(state.last_final).is_file()
    button("Xem nhanh 960×540").invoke()
    wait_done()
    assert (state.directory / "generated/output/preview.mp4").is_file()
    button("Tạo / cập nhật lời đọc Nhật").invoke()
    wait_done()
    root.destroy()
