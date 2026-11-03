from .models import Caption, Placement, Timeline

__all__ = ["Caption", "Placement", "Timeline", "TimelineService"]


def __getattr__(name):
    if name == "TimelineService":
        from .service import TimelineService
        return TimelineService
    raise AttributeError(name)
