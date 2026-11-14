"""Read-only UIA feasibility checks; no Google session or Windows desktop in CI."""
from types import SimpleNamespace

import pytest

from te.studio.channel_probe import (channel_id_from_url, list_studio_windows,
                                     parse_visible_links, probe_studio_window)

A = "UC" + "a" * 22
B = "UC" + "b" * 22


class Element:
    def __init__(self, name, value=""):
        self.element_info = SimpleNamespace(name=name)
        self.value = value
    def get_value(self):
        return self.value


class Window:
    def __init__(self, hwnd, title, edits=(), links=()):
        self.handle = hwnd
        self.title = title
        self.edits = list(edits)
        self.links = list(links)
    def window_text(self):
        return self.title
    def descendants(self, control_type):
        return self.edits if control_type == "Edit" else self.links
    def wrapper_object(self):
        return self


class Desktop:
    def __init__(self, windows):
        self.items = windows
    def windows(self):
        return self.items
    def window(self, handle):
        return next(item for item in self.items if item.handle == handle)


def test_strict_studio_url_and_missing_id():
    assert channel_id_from_url(f"https://studio.youtube.com/channel/{A}/videos?filter=1") == A
    for url in ("", "Studio", f"https://studio.youtube.com/channel/{A[:-1]}",
                f"https://studio.youtube.com.evil.test/channel/{A}",
                f"http://studio.youtube.com/channel/{A}",
                f"https://youtube.com/channel/{A}"):
        assert channel_id_from_url(url) is None


def test_candidate_parser_preserves_duplicate_names_but_never_invents_id():
    links = [("同じ名前", f"https://studio.youtube.com/channel/{A}"),
             ("同じ名前", f"https://studio.youtube.com/channel/{B}/videos"),
             ("Name without ID", "https://studio.youtube.com/channel_switcher"),
             ("Other account", f"https://youtube.com/channel/{A}")]
    assert parse_visible_links(links) == [
        {"id": A, "names": ["同じ名前"], "state": "unverified"},
        {"id": B, "names": ["同じ名前"], "state": "unverified"}]
    assert parse_visible_links([]) == []


def test_multiple_windows_require_explicit_studio_handle_and_no_other_tabs():
    studio_a = Window(7, "Channel A - YouTube Studio",
                      [Element("Address", f"https://studio.youtube.com/channel/{A}")])
    unrelated = Window(8, "Private email - Google Chrome", [Element("Address", "https://mail.google.com/")])
    studio_b = Window(9, "Channel B - YouTube Studio",
                      [Element("Address", f"https://studio.youtube.com/channel/{B}")])
    desktop = Desktop([studio_a, unrelated, studio_b])
    assert [item["hwnd"] for item in list_studio_windows(desktop)] == [7, 9]
    assert probe_studio_window(9, desktop)["active_channel_id"] == B
    assert probe_studio_window(7, desktop)["active_channel_id"] == A
    with pytest.raises(ValueError, match="không hiển thị YouTube Studio"):
        probe_studio_window(8, desktop)
    with pytest.raises(ValueError, match="Không tìm thấy cửa sổ"):
        probe_studio_window(404, desktop)


def test_probe_marks_inaccessible_or_ambiguous_windows_as_failure():
    no_id = Window(3, "YouTube Studio", [Element("Address", "https://studio.youtube.com/")])
    changed = Window(4, "YouTube Studio", [Element("Address", f"https://studio.youtube.com/channel/{A}"),
                                            Element("Address", f"https://studio.youtube.com/channel/{B}")])
    desktop = Desktop([no_id, changed])
    with pytest.raises(ValueError, match="duy nhất"):
        probe_studio_window(3, desktop)
    with pytest.raises(ValueError, match="duy nhất"):
        probe_studio_window(4, desktop)
    assert list_studio_windows(Desktop([])) == []


def test_probe_is_read_only_and_cancellable():
    window = Window(5, "YouTube Studio",
                    [Element("Address", f"https://studio.youtube.com/channel/{A}")],
                    [Element("同じ名前", f"https://studio.youtube.com/channel/{B}")])
    result = probe_studio_window(5, Desktop([window]))
    assert result["url_anchored_candidates"] == [
        {"id": B, "names": ["同じ名前"], "state": "unverified"}]
    assert result["verified_upload_permission"] is False
    assert result["complete_channel_list"] is False
    with pytest.raises(RuntimeError, match="Đã hủy"):
        probe_studio_window(5, Desktop([window]), cancelled=lambda: True)
