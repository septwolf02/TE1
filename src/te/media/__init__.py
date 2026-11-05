from .service import (
    MediaAssignmentService, MediaLibraryService, MediaScanResult, ReferenceValidation,
)

__all__ = [
    "MediaAssignmentService", "MediaLibraryService", "MediaScanResult", "ReferenceValidation",
    "KeyContext", "KeyPlacementService",
]


def __getattr__(name):
    if name in ("KeyContext", "KeyPlacementService"):
        from .key_service import KeyContext, KeyPlacementService
        return {"KeyContext": KeyContext, "KeyPlacementService": KeyPlacementService}[name]
    raise AttributeError(name)
