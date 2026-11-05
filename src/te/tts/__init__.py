from .service import FakeTTSProvider, TTSProvider, TTSService
from .voicevox import VoicevoxError, VoicevoxProvider

__all__ = ["FakeTTSProvider", "TTSProvider", "TTSService", "VoicevoxProvider", "VoicevoxError", "SentenceSpeechService"]


def __getattr__(name):
    if name == "SentenceSpeechService":
        from .sentences import SentenceSpeechService
        return SentenceSpeechService
    raise AttributeError(name)
