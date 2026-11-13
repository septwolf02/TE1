"""Timeline contract shared by manual and future AI media selectors."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from te.project.models import Project, SectionName, valid_id

TIMELINE_VERSION = 2
EFFECTS = frozenset({"none", "slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"})
TRANSITIONS = frozenset({"cut", "fade", "crossfade"})
VIDEO_POLICIES = frozenset({"trim_to_segment", "loop_to_segment", "freeze_last_frame", "keep_original"})


@dataclass(slots=True)
class Caption:
    id: str
    segment_id: str
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class Placement:
    id: str
    media_id: str
    section: SectionName
    start_ms: int
    end_ms: int
    segment_id: str | None = None
    effects: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class SegmentBlock:
    segment_id: str
    section: SectionName
    start_ms: int
    end_ms: int
    audio_path: str
    media_id: str | None
    visual_status: str
    caption_ids: list[str]
    effect: str = "none"
    effect_locked: bool = False
    visual_locked: bool = False
    video_policy: str = "trim_to_segment"
    transition: str = "cut"


@dataclass(slots=True)
class IntroEvent:
    section: SectionName
    media_id: str
    start_ms: int
    end_ms: int
    duration_policy: str = "keep_original"


@dataclass(slots=True)
class TransitionEvent:
    from_section: SectionName
    to_section: SectionName
    at_ms: int
    preset: str = "cut"


@dataclass(slots=True)
class SectionMusic:
    section: SectionName
    path: str
    volume: float = 0.2
    fade_in_ms: int = 0
    fade_out_ms: int = 0


@dataclass(slots=True)
class Timeline:
    project_id: str
    captions: list[Caption] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    blocks: list[SegmentBlock] = field(default_factory=list)
    intros: list[IntroEvent] = field(default_factory=list)
    transitions: list[TransitionEvent] = field(default_factory=list)
    music: list[SectionMusic] = field(default_factory=list)
    headline: dict = field(default_factory=lambda: {"enabled": True, "position": "center", "darkening": 0.7,
                                                    "duration_seconds": 7})
    source_fingerprint: str | None = None
    schema_version: int = TIMELINE_VERSION

    def validate(self, project: Project | None = None) -> None:
        if self.schema_version != TIMELINE_VERSION or not valid_id(self.project_id, "prj"):
            raise ValueError("Unsupported timeline version or invalid project ID")
        if project and self.project_id != project.id:
            raise ValueError("Timeline belongs to another project")
        segment_ids = {s.id for s in project.segments} if project else None
        media_by_id = {m.id: m for m in project.media} if project else None
        segment_by_id = {s.id: s for s in project.segments} if project else None
        seen: set[str] = set()
        for caption in self.captions:
            if not valid_id(caption.id, "cap") or caption.id in seen:
                raise ValueError("Invalid or duplicate caption ID")
            seen.add(caption.id)
            if not valid_id(caption.segment_id, "seg") or (segment_ids is not None and caption.segment_id not in segment_ids):
                raise ValueError("Unknown caption segment")
            if not caption.text.strip() or caption.start_ms < 0 or caption.end_ms <= caption.start_ms:
                raise ValueError("Invalid caption timing or text")
        seen.clear()
        for placement in self.placements:
            if not valid_id(placement.id, "plc") or placement.id in seen:
                raise ValueError("Invalid or duplicate placement ID")
            seen.add(placement.id)
            if not valid_id(placement.media_id, "med") or (media_by_id is not None and placement.media_id not in media_by_id):
                raise ValueError("Unknown placement media")
            if placement.segment_id and (not valid_id(placement.segment_id, "seg") or
                    (segment_ids is not None and placement.segment_id not in segment_ids)):
                raise ValueError("Unknown placement segment")
            if placement.start_ms < 0 or placement.end_ms <= placement.start_ms:
                raise ValueError("Invalid placement timing")
            if media_by_id is not None:
                asset = media_by_id[placement.media_id]
                if asset.section is not None and asset.section != placement.section:
                    raise ValueError("Intro video placement must match its section")
            if segment_by_id is not None and placement.segment_id is not None:
                if segment_by_id[placement.segment_id].section != placement.section:
                    raise ValueError("Placement must match its segment section")
        cursor = 0
        blocks_by_id = {}
        for block in self.blocks:
            if block.segment_id in blocks_by_id or not valid_id(block.segment_id, "seg"):
                raise ValueError("Duplicate or invalid segment block")
            if block.start_ms < cursor or block.end_ms <= block.start_ms:
                raise ValueError("Overlapping or invalid segment blocks")
            if block.effect not in EFFECTS or block.transition not in TRANSITIONS or block.video_policy not in VIDEO_POLICIES:
                raise ValueError("Invalid effect, transition or video policy")
            if segment_by_id is not None and (block.segment_id not in segment_by_id or segment_by_id[block.segment_id].section != block.section):
                raise ValueError("Invalid segment block reference")
            blocks_by_id[block.segment_id] = block
            cursor = block.end_ms
        for intro in self.intros:
            if intro.start_ms < 0 or intro.end_ms <= intro.start_ms or intro.duration_policy not in ("keep_original", "trim"):
                raise ValueError("Invalid section intro timing")
            if media_by_id is not None:
                asset = media_by_id.get(intro.media_id)
                if asset is None or asset.section != intro.section:
                    raise ValueError("Invalid section intro placement")
        for transition in self.transitions:
            if transition.preset not in TRANSITIONS or transition.at_ms < 0:
                raise ValueError("Invalid transition")
        for music in self.music:
            if not music.path or not 0 <= music.volume <= 1 or music.fade_in_ms < 0 or music.fade_out_ms < 0:
                raise ValueError("Invalid section music")
        if not 0 <= self.headline.get("darkening", -1) <= 1:
            raise ValueError("Invalid headline darkening")
        duration = self.headline.get("duration_seconds", 7)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 5 <= duration <= 10:
            raise ValueError("Headline duration must be between 5 and 10 seconds")
        for caption in self.captions:
            block = blocks_by_id.get(caption.segment_id)
            if block and not (block.start_ms <= caption.start_ms < caption.end_ms <= block.end_ms):
                raise ValueError("Caption outside parent segment")
        if self.blocks:
            events = sorted([(b.start_ms, b.end_ms) for b in self.blocks] +
                            [(i.start_ms, i.end_ms) for i in self.intros])
            if events[0][0] != 0:
                raise ValueError("Timeline has an audio gap or overlap")
            covered = events[0][1]
            for start, end in events[1:]:
                if start > covered:
                    raise ValueError("Timeline has an audio gap or overlap")
                covered = max(covered, end)
            for intro in self.intros:
                overlapping = [block for block in self.blocks
                               if block.start_ms < intro.end_ms and intro.start_ms < block.end_ms]
                if overlapping:
                    section_blocks = [block for block in self.blocks if block.section == intro.section]
                    if not section_blocks or intro.start_ms != section_blocks[0].start_ms or intro.end_ms > section_blocks[-1].end_ms:
                        raise ValueError("Intro overlap must stay within its section and begin with narration")

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, project: Project | None = None) -> Timeline:
        try:
            version = data["schema_version"]
            if version not in (1, TIMELINE_VERSION):
                raise ValueError("Unsupported timeline version")
            timeline = cls(project_id=data["project_id"], schema_version=TIMELINE_VERSION,
                captions=[Caption(**c) for c in data["captions"]],
                placements=[Placement(id=p["id"], media_id=p["media_id"], section=SectionName(p["section"]),
                    start_ms=p["start_ms"], end_ms=p["end_ms"], segment_id=p.get("segment_id"),
                    effects=p.get("effects", [])) for p in data["placements"]],
                blocks=[SegmentBlock(**{**b, "section": SectionName(b["section"])}) for b in data.get("blocks", [])],
                intros=[IntroEvent(**{**i, "section": SectionName(i["section"])}) for i in data.get("intros", [])],
                transitions=[TransitionEvent(**{**t, "from_section": SectionName(t["from_section"]),
                    "to_section": SectionName(t["to_section"])}) for t in data.get("transitions", [])],
                music=[SectionMusic(**{**m, "section": SectionName(m["section"])}) for m in data.get("music", [])],
                headline=data.get("headline", {"enabled": True, "position": "center", "darkening": 0.7}),
                source_fingerprint=data.get("source_fingerprint"))
            timeline.validate(project)
            return timeline
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid timeline.json: {exc}") from exc
