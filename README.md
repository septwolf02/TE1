# TE — Sports Video Editor

Local-first Windows application for Japanese sports storytelling videos. TE provides Python 3.12 project data and local FFmpeg rendering. Fake TTS supports offline tests and VOICEVOX supports real Japanese speech through a locally running engine.

## Get started

See [Windows setup](docs/windows-setup.md). The package has no runtime dependencies. A project is a directory containing `project.json` and `timeline.json`.

```python
from pathlib import Path
from te.project import ProjectManager, SectionName

manager = ProjectManager()
folder = Path.home() / "Videos" / "TE" / "first-project"
project = manager.create(folder, "My story")
manager.add_segment(project, SectionName.P1, 1, "日本語のナレーション。", "Lời dẫn tham khảo")
manager.save(folder, project)
project = manager.open(folder)
```

Media can be registered with `manager.add_media(project, MediaKind.KEY_IMAGE, path)` (import `MediaKind` from `te.project.models`). The stored absolute path points to the original local file. TE never copies or edits that file. Keep files at their paths when reopening a project.

## Data contracts

The current contracts are [project v8](src/te/schemas/project-v8.json) and [timeline v2](src/te/schemas/timeline-v2.json). IDs use `prj_`, `seg_`, `med_`, `cap_`, and `plc_` plus a 32-character UUID hex value. IDs remain stable when segment text, order, or indexed media metadata changes. Segment numbers are positive and unique across the project; sections are exactly P1–P4. Captions point to segments; placements point to registered media. Timing uses integer milliseconds, with inclusive start and exclusive end. Project v1–v4 and timeline v1 files are migrated in memory.

`te.interfaces` defines the ports for script parsing, speech synthesis, media listing/selection, subtitle alignment, timeline building, effects and rendering. All selectors output the same `Placement` objects, so timeline and rendering do not depend on selection method. Implementations will be added later. Project data stores references and editing intent, while the timeline stores timed output decisions.

## Status

Modules 0–6 cover project persistence, script input/editing, local media indexing, primary visual assignments, segment TTS, sentence-level caption alignment, timeline construction and local FFmpeg rendering. No GUI, real forced alignment, AI recognition/selection or YouTube integration is implemented.

## Module 1: script input and editing

`te.script.ScriptService` imports UTF-8 narration files with `[P1]` through `[P4]` markers and globally sequential `1. text` or `1) text` segments. All four markers are required, in order. Text after the number delimiter is preserved, including extra spaces and internal line breaks; the terminal line separator is treated as a record separator. Source files are read only. An optional Vietnamese file uses the same format and aligns only if segment counts, numbers and sections match exactly. A mismatch returns a warning and assigns no Vietnamese text.

The service supports `add`, `delete`, `split`, `merge`, `reorder`, `move`, and `edit`. `number` is the 1-based order index; IDs stay stable where possible. Splitting aligned Vietnamese or already referenced content needs an explicit user decision, so the service rejects that operation. Merge preserves the first ID and concatenates both texts directly. `ProjectManager.save` checks the existing timeline against edited segments before writing.

Module 1 introduced [project v2](src/te/schemas/project-v2.json). Opening a Module 0 v1 project fills its segment fields with defaults; Module 2 then migrates either version to the current v3 contract. Caption units can outnumber narration segments because each caption has its own ID and a `segment_id` reference.

## Module 2: media library and assignments

`MediaLibraryService.scan_library` recursively indexes a selected external folder. Supported images are JPG, JPEG, PNG and WebP; supported videos are MP4, MOV, MKV and WebM. The recommended `normal/`, `key/`, and `section_intro/P1.mp4`–`P4.mp4` convention is detected automatically, but ordinary media may live anywhere under the selected folder. Metadata stays in `project.json`; source files are only opened for reading. Image dimensions are read from headers. Video dimensions and duration are populated when `ffprobe` is available and remain unknown otherwise.

Refresh preserves media IDs by absolute path and retains missing records as unavailable. `MediaAssignmentService` supports one primary visual per segment, assignment origin, locking, effect preference and notes. KEY assignments define the reveal segment; `is_media_allowed_at` gives future selectors a guard against early use. Section intro clips are stored in a separate P1–P4 mapping. `media_reuse_distance` records the future reuse policy without implementing selection logic.

Project schema is now [v3](src/te/schemas/project-v3.json). Module 0 v1 and Module 1 v2 projects receive media defaults while loading and are saved as v3. Timeline remains v1. Caption units can outnumber narration segments because each caption has its own ID and a `segment_id` reference.

## Module 3: Japanese TTS engine

`TTSService` takes a project directory and a segment ID. Its provider interface receives the exact stored Japanese narration and writes a WAV. The included `FakeTTSProvider` creates deterministic silent WAV files for offline tests. Register another provider by ID when an external adapter is available. Project defaults cover provider, voice, speaking rate, style, WAV format and sample rate; `generate_segment` also accepts an explicit settings override.

Audio is saved as `generated/audio/<segment_id>.wav` inside the project. The file name stays stable when segments are reordered. Each generation records provider and voice IDs, settings, measured WAV duration, UTC timestamp, version, original/provider text, SHA-256 text hash, status and error. The service saves `project.json` through `ProjectManager` after each result. `generate_all` generates missing, stale and failed segments; `regenerate_segment` and `regenerate_selected` explicitly rerun chosen segments. A failed regeneration preserves the previous WAV and marks only that segment failed. `get_audio_status`, `detect_stale_audio` and `validate_audio` expose validity for later modules.

Project schema is now [v4](src/te/schemas/project-v4.json). Older project v1–v3 files load with default TTS settings and empty audio records, then save as v4. Generated WAV files are ignored by Git.

## Module 4: Japanese subtitles and alignment

`SubtitleService` splits Japanese narration at `。！？`, retaining closing Japanese quotation marks with the preceding sentence. Caption text remains unchanged by display wrapping. Each caption has a stable ID, parent segment/section, local audio timing, optional future absolute timing, alignment metadata, notes, lock and presentation (`default` or `headline`). The first two captions of the first P1 segment default to `headline`. Style settings are stored separately from caption timing.

The replaceable `AlignmentProvider` interface has a deterministic test provider and an approximate proportional fallback. Alignment requires current TTS audio. It uses the parent audio duration, stores relative milliseconds and saves the project after alignment. Text, audio fingerprint, duration, generation version and caption split changes make an alignment stale. Changed narration requires `regenerate_captions` before realignment; unchanged sentence positions retain their IDs. Locked captions are preserved unless overwrite is explicit. Manual text/timing edits and caption split/merge are available through the service. `wrap_caption` is a view-only helper that enforces at most two lines.

Project schema is now [v5](src/te/schemas/project-v5.json); v1–v4 migrate on load. Timeline remains v1 because these caption times belong to segment audio, not the final global timeline.

## Module 5: timeline and storytelling

`TimelineService.build_timeline` creates a deterministic audio-led timeline. Each segment block lasts exactly its narration WAV duration. Optional section intros are separate events before that section's narration; they keep their probed duration unless a trim limit is set. Section transitions are metadata at boundaries. Segment-relative captions become absolute timeline captions without changing their source timing.

The timeline stores segment blocks, intro events, transitions, visual placements, optional section music and P1 headline configuration. Missing visuals produce warnings and valid blocks. Missing/stale TTS or subtitles stop the build. KEY media obeys its reveal guard. Image effects follow a configurable sequence unless an assignment preference or locked prior choice applies. Video policy metadata supports trim, loop, freeze and keep-original; keep-original requires a video duration matching the narration block. `rebuild_timeline` retains locked effects, transition presets, music and headline metadata. `detect_stale_timeline` compares the current project to the saved source fingerprint. The current [timeline v2](src/te/schemas/timeline-v2.json) migrates v1; project v5 is unchanged. No rendering or audio mixing occurs.

## Module 6: local rendering

`RenderService` consumes an existing timeline and uses local FFmpeg/ffprobe. It normalizes each visual event into a cached H.264 clip, concatenates clips, mixes narration WAVs at their absolute start times with optional quiet section music, burns captions using ASS/libass, and writes a 1920×1080, 30 fps H.264/yuv420p + AAC MP4 under `generated/output/`. Preview uses 960×540 and a faster encoder preset. Source clip audio is omitted; narration stays the primary audio.

Still effects use deterministic FFmpeg zoom/pan filters. Cut is an immediate boundary; fade and crossfade presets use short fade-out/fade-in envelopes at the fixed boundary without temporal overlap, so total duration and narration timing stay unchanged. P1 headline events use centered large text over a darkened frame. Missing visuals render black, while unavailable narration, intro or music causes an explicit error. Final outputs are protected from overwrite unless `overwrite=True` is passed. The service exposes progress callbacks, cancellation, input validation, status, cache clearing and a render manifest. External media files are read only.

## TE V1 demo

Run `python -m te.demo` to create a new self-contained project under `demo/test/` and render a 960×540 preview. Add `--full` to render the 1920×1080 final MP4 in the same new project. The demo generates six images (one KEY), a P1 intro, simple background music, six fake narration WAVs, Japanese captions, four effect types, a P1→P2 fade and headline captions. It prints project and MP4 paths and leaves all source files untouched. See [Windows setup](docs/windows-setup.md) for exact PowerShell commands and font configuration.

## TTS-1: local VOICEVOX

`VoicevoxProvider` uses the locally running VOICEVOX Engine HTTP API. Speaker and style names resolve dynamically through `/speakers`; the selected speaker credit, policy text and `/speaker_info` reference are saved with each audio record. Project v6 adds voice presets, segment overrides and synthesis settings; v1–v5 projects migrate on load. Audio becomes stale when narration, provider, voice, style or settings change. Fake TTS remains available. See [Windows setup](docs/windows-setup.md#voicevox-tts-on-windows) for PowerShell check, list and sample commands.

## TTS-2: sentence-level narration

`SentenceSpeechService` splits each segment without rewriting its Japanese text, assigns stable `sen_` IDs, generates/reuses one WAV per sentence, and assembles an exact-duration segment WAV for the existing renderer. Project v7 stores sentence units, confirmed Vietnamese references, per-sentence audio metadata, and a configurable inter-sentence pause. Captions retain editable text but follow measured sentence audio boundaries; the timeline exposes absolute sentence ranges through `TimelineService.get_sentence_range`. Existing project v1–v6 data is migrated on load. See [Windows setup](docs/windows-setup.md#tts-2-sentence-level-voicevox-smoke-test).

## TTS-3: confirmed KEY reveal

`KeyPlacementService` links a marked KEY image to a confirmed Vietnamese/Japanese sentence mapping and stores an offset, duration and lock. Project v8 also stores a random seed for ordinary-image assignment; it excludes KEY media and retains saved choices. The audio-driven timeline resolves the sentence ID on each rebuild and partitions visual spans at the reveal. The renderer starts a KEY image on the first frame at or after its millisecond reveal time. Removed sentences or changed mappings mark placements for manual review. Older project v1–v7 files migrate on load. Run the self-contained [KEY demo](docs/windows-setup.md#tts-3-voicevox-key-reveal-demo) with a locally running VOICEVOX Engine.
