"""Parse complete bilingual scripts without changing their section bodies."""
from __future__ import annotations

from dataclasses import dataclass
import re

from te.project.models import SectionName


MARKER = re.compile(r"^\s*(P[1-4])\s*$", re.IGNORECASE)


def parse_full_script(text: str) -> dict[str, str]:
    """Return only labeled sections; retain all characters between marker lines."""
    result: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    previous_index = 0
    for line in text.splitlines(keepends=True):
        marker = MARKER.fullmatch(line.rstrip("\r\n"))
        if marker:
            label = marker.group(1).upper()
            index = int(label[1])
            if label in result or label == current:
                raise ValueError(f"Trùng nhãn {label}.")
            if index <= previous_index:
                raise ValueError(f"Nhãn {label} không đúng thứ tự P1–P4.")
            if current is None:
                if "".join(body).strip():
                    raise ValueError("Có nội dung trước nhãn P đầu tiên.")
            else:
                result[current] = "".join(body)
            current, previous_index, body = label, index, []
        else:
            body.append(line)
    if current is None:
        if text.strip():
            raise ValueError("Có nội dung trước nhãn P đầu tiên.")
        raise ValueError("Cần ít nhất một nhãn P1–P4.")
    result[current] = "".join(body)
    return result


@dataclass(frozen=True)
class FullScriptImportPlan:
    sections: dict[str, dict[str, str]]
    vietnamese_labels: tuple[str, ...]
    japanese_labels: tuple[str, ...]
    warnings: tuple[str, ...]
    overwritten: tuple[str, ...]
    cleared: tuple[str, ...]
    key_review: tuple[str, ...]
    collapsed: tuple[str, ...]


def alignment_warnings(vietnamese: dict[str, str], japanese: dict[str, str]) -> tuple[str, ...]:
    missing_vi = [s.value for s in SectionName if s.value in japanese and s.value not in vietnamese]
    missing_ja = [s.value for s in SectionName if s.value in vietnamese and s.value not in japanese]
    if not missing_vi and not missing_ja:
        return ()
    return ("Nhãn Việt/Nhật khác nhau. Thiếu trong bản Việt: " + (", ".join(missing_vi) or "không có") +
            "; thiếu trong bản Nhật: " + (", ".join(missing_ja) or "không có") + ".",)
