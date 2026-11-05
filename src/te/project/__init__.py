from .models import (
    AssignmentType, AudioRecord, KeyPlacement, MediaAsset, MediaAssignment, MediaCategory,
    MediaKind, MediaType, Project, ScriptSegment, Section, SectionName, TTSSettings,
)

__all__ = [
    "AssignmentType", "AudioRecord", "MediaAsset", "MediaAssignment", "MediaCategory",
    "MediaKind", "MediaType", "ProjectManager", "Project", "ScriptSegment", "Section",
    "SectionName", "TTSSettings",
    "KeyPlacement",
]


def __getattr__(name):
    if name == "ProjectManager":
        from .manager import ProjectManager
        return ProjectManager
    raise AttributeError(name)
