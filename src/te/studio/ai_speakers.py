"""Provider-independent, non-destructive speaker review records."""
from __future__ import annotations

from hashlib import sha256
import json

from te.project.models import SectionName


ROLES = {"A": "MC / Narrator", "B": "Main story participant",
         "C": "Commentator C", "D": "Commentator D"}

RESULT_FILTERS = ("Toàn bài", "P1", "P2", "P3", "P4")
OPEN_QUOTES = "‘“\"「『"
CLOSE_QUOTES = "’”\"」』"
ENDINGS = ".!?…。！？"


def segment_passages(sections: dict) -> dict[str, dict]:
    """Make stable IDs for exact Unicode spans; never normalize source script text."""
    result = {}
    for section in SectionName:
        content = sections.get(section.value, {"vi": "", "ja": ""})
        for language in ("vi", "ja"):
            source = content[language]
            start = 0
            spans = []
            def add(end: int) -> None:
                nonlocal start
                if end > start and source[start:end].strip():
                    value = source[start:end]
                    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
                    identifier = f"{section.value}:{language}:{start}:{end}:{digest}"
                    spans.append({"id": identifier, "section": section.value,
                                  "language": language, "start": start, "end": end, "text": value})
                start = end
            for index, char in enumerate(source):
                if char in OPEN_QUOTES and index > start and source[start:index].strip():
                    add(index)
                if (char in CLOSE_QUOTES or char == "\n" or
                        (char in ENDINGS and (index + 1 == len(source) or source[index + 1] not in CLOSE_QUOTES))):
                    add(index + 1)
            add(len(source))
            for span in spans:
                result[span["id"]] = span
    return result


def filter_assignments(rows: list[dict], selected: str) -> list[dict]:
    if selected not in RESULT_FILTERS:
        raise ValueError("Bộ lọc kết quả không hợp lệ.")
    order = {section.value: index for index, section in enumerate(SectionName)}
    return sorted((row for row in rows if selected == "Toàn bài" or row["section"] == selected),
                  key=lambda row: (order[row["section"]], row["vietnamese"][0]))


def section_counts(rows: list[dict]) -> dict[str, int]:
    return {section.value: sum(row["section"] == section.value for row in rows)
            for section in SectionName}


def resolve_speaker_references(raw: object, sections: dict) -> tuple[list[dict], tuple[str, ...]]:
    """Keep valid rows and turn missing/invalid references into explicit review rows."""
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("Trường assignments không phải danh sách hợp lệ.")
    passages = segment_passages(sections)
    vietnamese = {key: value for key, value in passages.items() if value["language"] == "vi"}
    japanese = {key: value for key, value in passages.items() if value["language"] == "ja"}
    resolved = []
    seen = set()
    diagnostics = []
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            diagnostics.append(f"Dòng {index}: định dạng không hợp lệ.")
            continue
        vi_id = row.get("vietnamese_id")
        vi = vietnamese.get(vi_id)
        if vi is None:
            diagnostics.append(f"Dòng {index}: ID đoạn Việt không tồn tại.")
            continue
        if vi_id in seen:
            diagnostics.append(f"Dòng {index}: ID đoạn Việt bị lặp.")
            continue
        seen.add(vi_id)
        role = row.get("role")
        warning = row.get("warning", "")
        if not isinstance(warning, str):
            warning = "Cảnh báo AI không hợp lệ."
        warning = warning[:300]
        if role not in ROLES:
            diagnostics.append(f"Dòng {index}: vai không hỗ trợ.")
            warning = "Vai AI không hợp lệ; chọn A/B/C/D thủ công. " + warning
            role = None
        ids = row.get("japanese_ids")
        ja_span = None
        if ids is not None:
            if isinstance(ids, list) and ids and all(isinstance(item, str) for item in ids):
                parts = [japanese.get(item) for item in ids]
                ordered = sorted((part for part in japanese.values() if part["section"] == vi["section"]),
                                 key=lambda part: part["start"])
                positions = [ordered.index(part) if part in ordered else -1 for part in parts]
                if (all(part is not None and part["section"] == vi["section"] for part in parts)
                        and positions == list(range(positions[0], positions[0] + len(positions)))):
                    ja_span = [parts[0]["start"], parts[-1]["end"]]
            if ja_span is None:
                diagnostics.append(f"Dòng {index}: tham chiếu Nhật không hợp lệ.")
                warning = "Chưa xác định được đoạn Nhật; chọn thủ công. " + warning
        else:
            warning = "Chưa căn chỉnh được đoạn Nhật; chọn thủ công. " + warning
        confidence = row.get("confidence")
        if confidence not in ("high", "review"):
            confidence = "review"
        if role is None or ja_span is None:
            confidence = "review"
        resolved.append({"section": vi["section"], "role": role,
                         "vietnamese": [vi["start"], vi["end"]], "japanese": ja_span,
                         "confidence": confidence, "warning": warning,
                         "confirmed": False, "source": "ai"})
    for vi_id, vi in vietnamese.items():
        if vi_id not in seen:
            resolved.append({"section": vi["section"], "role": None,
                             "vietnamese": [vi["start"], vi["end"]], "japanese": None,
                             "confidence": "review", "warning": "AI bỏ sót đoạn Việt; phân vai thủ công.",
                             "confirmed": False, "source": "ai"})
            diagnostics.append("Có đoạn Việt chưa được AI phân vai.")
    order = {section.value: index for index, section in enumerate(SectionName)}
    resolved.sort(key=lambda row: (order[row["section"]], row["vietnamese"][0]))
    return validate_assignments(resolved, sections), tuple(dict.fromkeys(diagnostics))


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
