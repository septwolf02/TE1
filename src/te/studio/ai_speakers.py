"""Provider-independent, non-destructive speaker review records."""
from __future__ import annotations

from hashlib import sha256
import json

from te.project.models import SectionName


ROLES = {"A": "MC / Narrator", "B": "Main story participant",
         "C": "Commentator C", "D": "Commentator D"}


def script_fingerprint(sections: dict) -> str:
    source = [[s.value, sections[s.value]["vi"], sections[s.value]["ja"]] for s in SectionName]
    return sha256(json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def default_speaker_data() -> dict:
    return {"identities": dict(ROLES), "assignments": [], "voices": {},
            "fingerprint": None, "status": "unreviewed"}


def validate_assignments(raw: object, sections: dict, *, strict: bool = True) -> list[dict]:
    """Reject invented text, invalid offsets and unsupported roles before project storage."""
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("AI trả về danh sách phân vai không hợp lệ.")
    result = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Bản ghi phân vai không hợp lệ.")
        section = row.get("section")
        if section not in {item.value for item in SectionName}:
            raise ValueError("Phần P1–P4 không hợp lệ.")
        role = row.get("role")
        if role not in ROLES and role is not None:
            raise ValueError("Vai phải là A, B, C, D hoặc chưa rõ.")
        offsets = {}
        for language, key in (("vi", "vietnamese"), ("ja", "japanese")):
            span = row.get(key)
            if span is None and language == "ja":
                offsets[language] = None
                continue
            if (not isinstance(span, list) or len(span) != 2 or
                    any(type(value) is not int for value in span)):
                raise ValueError(f"Vị trí {language} không hợp lệ.")
            start, end = span
            source = sections[section][language]
            if not 0 <= start < end or (strict and (end > len(source) or not source[start:end].strip())):
                raise ValueError(f"Vị trí {language} nằm ngoài script.")
            offsets[language] = [start, end]
        confidence = row.get("confidence", "review")
        if confidence not in ("high", "review"):
            raise ValueError("Mức chắc chắn không hợp lệ.")
        warning = row.get("warning", "")
        if not isinstance(warning, str) or len(warning) > 500:
            raise ValueError("Cảnh báo không hợp lệ.")
        if (offsets["ja"] is None or role is None) and confidence == "high":
            raise ValueError("Thiếu vai hoặc căn chỉnh Nhật phải cần xem lại.")
        result.append({"section": section, "role": role, "vietnamese": offsets["vi"],
                       "japanese": offsets["ja"], "confidence": confidence,
                       "warning": warning, "confirmed": False, "source": "ai"})
    return result


def passage(sections: dict, row: dict, language: str) -> str:
    span = row["vietnamese" if language == "vi" else "japanese"]
    return sections[row["section"]][language][span[0]:span[1]] if span else ""


def validate_speaker_data(data: dict, sections: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Dữ liệu phân vai không hợp lệ.")
    result = default_speaker_data()
    identities = data.get("identities", result["identities"])
    if not isinstance(identities, dict) or set(identities) != set(ROLES) or any(
            not isinstance(value, str) or len(value) > 120 for value in identities.values()):
        raise ValueError("Tên nhân vật A/B/C/D không hợp lệ.")
    result["identities"] = dict(identities)
    fingerprint = data.get("fingerprint")
    stale = isinstance(fingerprint, str) and fingerprint != script_fingerprint(sections)
    result["assignments"] = validate_assignments(data.get("assignments", []), sections, strict=not stale)
    for row, saved in zip(result["assignments"], data.get("assignments", [])):
        row["confirmed"] = saved.get("confirmed") is True
        row["source"] = saved.get("source") if saved.get("source") in ("ai", "manual") else "ai"
    voices = data.get("voices", {})
    if not isinstance(voices, dict) or any(role not in ROLES or not isinstance(value, dict) or
            not isinstance(value.get("style_id"), int) or not isinstance(value.get("speaker_uuid"), str)
            for role, value in voices.items()):
        raise ValueError("Giọng VOICEVOX theo vai không hợp lệ.")
    result["voices"] = voices
    result["fingerprint"] = fingerprint if isinstance(fingerprint, str) else None
    result["status"] = data.get("status") if data.get("status") in (
        "unreviewed", "draft", "confirmed", "stale") else "unreviewed"
    if stale:
        result["status"] = "stale"
    return result
