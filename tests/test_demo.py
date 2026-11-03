from pathlib import Path

from te.demo import run_demo
from te.project import AssignmentType, ProjectManager, SectionName
from te.timeline import TimelineService


def test_demo_builds_self_contained_preview(tmp_path):
    preview, full = run_demo(tmp_path)
    assert preview.is_file() and full is None
    project_dir = preview.parents[2]
    project = ProjectManager().open(project_dir)
    timeline = TimelineService.load_timeline(project, project_dir)
    assert len(project.segments) == 6
    assert [s.section for s in project.segments] == [SectionName.P1] * 3 + [SectionName.P2] * 3
    assert len([asset for asset in project.media if asset.media_type == "image"]) == 6
    assert len(timeline.intros) == 1 and timeline.intros[0].section == SectionName.P1
    assert len(project.audio_records) == 6
    assert all(record.path and Path(record.path).is_file() for record in project.audio_records)
    assert len(timeline.captions) >= 6
    assert len([a for a in project.media_assignments if a.assignment_type == AssignmentType.KEY and a.locked]) == 1
    assert {block.effect for block in timeline.blocks} >= {"slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right"}
    assert timeline.transitions[0].preset == "fade"
    assert timeline.music[0].section == SectionName.P1
    assert timeline.headline["darkening"] == 0.7
    assert all(Path(asset.path).is_relative_to(project_dir) for asset in project.media)
