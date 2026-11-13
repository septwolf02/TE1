"""Local VOICEVOX Engine adapter. Speaker/style numbers are discovered at runtime."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from te.project.models import TTSSettings


class VoicevoxError(RuntimeError):
    """A VOICEVOX connection, response, or selection failed."""


class VoicevoxProvider:
    provider_id = "voicevox"
    preferred_voices = ("青山龍星", "剣崎雌雄", "後鬼")

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    @staticmethod
    def _base(settings: TTSSettings) -> str:
        return f"http://{settings.host}:{settings.port}"

    def _request(self, settings: TTSSettings, method: str, endpoint: str,
                 data: bytes | None = None) -> bytes:
        url = self._base(settings) + endpoint
        request = Request(url, data=data, method=method,
                          headers={"Content-Type": "application/json; charset=utf-8"} if data is not None else {})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise VoicevoxError(f"VOICEVOX HTTP {exc.code} at {endpoint}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise VoicevoxError(f"Cannot connect to VOICEVOX at {self._base(settings)}: {exc}") from exc

    def _json(self, settings: TTSSettings, method: str, endpoint: str,
              data: bytes | None = None):
        try:
            return json.loads(self._request(settings, method, endpoint, data).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise VoicevoxError(f"Invalid VOICEVOX JSON response from {endpoint}") from exc

    def check_connection(self, settings: TTSSettings) -> str:
        return str(self._json(settings, "GET", "/version"))

    def list_voices(self, settings: TTSSettings) -> list[dict]:
        speakers = self._json(settings, "GET", "/speakers")
        if not isinstance(speakers, list):
            raise VoicevoxError("VOICEVOX /speakers returned an invalid response")
        return sorted(speakers, key=lambda item: (
            self.preferred_voices.index(item.get("name")) if item.get("name") in self.preferred_voices else len(self.preferred_voices),
            item.get("name", "")))

    def _select(self, settings: TTSSettings, style_id: int | None = None) -> tuple[dict, dict]:
        speaker = next((item for item in self.list_voices(settings)
                        if item.get("name") == settings.voice_id or item.get("speaker_uuid") == settings.voice_id), None)
        if speaker is None:
            raise VoicevoxError(f"VOICEVOX speaker not installed: {settings.voice_id}. Run 'python -m te.tts list'.")
        styles = speaker.get("styles", [])
        if not styles:
            raise VoicevoxError(f"VOICEVOX speaker has no styles: {settings.voice_id}")
        if style_id is not None:
            style = next((item for item in styles if item.get("id") == style_id), None)
        elif settings.style is None:
            style = next((item for item in styles if item.get("name") == "ノーマル"), styles[0])
        else:
            style = next((item for item in styles if item.get("name") == settings.style), None)
        if style is None or not isinstance(style.get("id"), int):
            raise VoicevoxError(f"VOICEVOX style '{settings.style}' is unavailable for {speaker['name']}")
        return speaker, style

    def synthesize(self, text: str, settings: TTSSettings, output_path: Path,
                   *, style_id: int | None = None) -> dict:
        if settings.output_format != "wav":
            raise ValueError("VOICEVOX output must be WAV")
        speaker, style = self._select(settings, style_id)
        style_id = style["id"]
        query_path = "/audio_query?" + urlencode({"text": text, "speaker": style_id})
        query = self._json(settings, "POST", query_path, b"")
        if not isinstance(query, dict):
            raise VoicevoxError("VOICEVOX /audio_query returned an invalid response")
        query.update(speedScale=settings.speaking_rate, pitchScale=settings.pitch,
                     intonationScale=settings.intonation, volumeScale=settings.volume,
                     outputSamplingRate=settings.sample_rate, outputStereo=False)
        audio = self._request(settings, "POST", "/synthesis?" + urlencode({"speaker": style_id}),
                              json.dumps(query, ensure_ascii=False).encode("utf-8"))
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise VoicevoxError("VOICEVOX /synthesis did not return WAV audio")
        output_path.write_bytes(audio)
        speaker_uuid = speaker.get("speaker_uuid")
        reference = (self._base(settings) + "/speaker_info?" + urlencode({"speaker_uuid": speaker_uuid})
                     if speaker_uuid else None)
        policy = speaker.get("policy")
        if speaker_uuid:
            info = self._json(settings, "GET", "/speaker_info?" + urlencode({"speaker_uuid": speaker_uuid}))
            if isinstance(info, dict):
                policy = info.get("policy", policy)
        return {"speaker_uuid": speaker_uuid, "style_id": style_id,
                "credit": speaker.get("name"), "license_reference": reference,
                "license_text": policy}
