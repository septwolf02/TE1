"""Machine-local role presets never mutate existing project attribution or voices implicitly."""
from copy import deepcopy
import json

import pytest

from te.project.models import SectionName
from te.studio.ai_speakers import ROLES, resolve_japanese_references, segment_passages
from te.studio.multi_voice import confirmed_speaker_data, speech_plan
from te.studio.service import StudioService


def _service(tmp_path):
    service = StudioService(tmp_path / "machine-settings.json")
    service.set_directories(tmp_path / "projects", tmp_path / "output")
    return service


def _voices():
    return {role: {"speaker_uuid": f"uuid-{role}", "style_id": index,
                   "speaker": f"Speaker {role}", "style": "Normal"}
            for index, role in enumerate(ROLES, 1)}


def test_save_restart_new_project_and_independent_overrides(tmp_path):
    service = _service(tmp_path)
    existing = service.create_project("Existing")
    source = service.create_project("Source")
    voices = _voices()
    source.speaker_data["voices"] = deepcopy(voices)
    source.speaker_data["parameters"]["B"] = {"speaking_rate": 1.3, "pitch": 0.04,
                                                   "intonation": 1.2, "volume": 0.8}
    service.save_project(source)
    original = deepcopy(source.speaker_data)
    service.save_default_role_voices(source)
    assert source.speaker_data == original
    machine_data = json.loads(service.config_path.read_text(encoding="utf-8"))
    assert set(machine_data["default_role_voices"]) == set(ROLES)
    assert machine_data["default_role_voices"]["B"]["speaking_rate"] == 1.3
    assert machine_data["default_role_voices"]["A"]["speaking_rate"] == source.project.tts_settings.speaking_rate
    assert "default_role_voices" not in (source.directory / "studio.json").read_text(encoding="utf-8")

    restarted = StudioService(service.config_path)
    new = restarted.create_project("New")
    assert new.speaker_data["voices"] == voices
    assert new.speaker_data["parameters"]["B"]["pitch"] == 0.04
    assert set(new.speaker_data["parameters"]) == set(ROLES)
    assert new.speaker_data["assignments"] == [] and new.speaker_data["status"] == "unreviewed"
    assert restarted.open_project(existing.directory).speaker_data["voices"] == {}
    restarted.save_role_configuration(new, "B", "Nhân vật mới", voices["A"])
    assert restarted.open_project(new.directory).speaker_data["voices"]["B"] == voices["A"]
    assert restarted.default_role_voices()["B"]["speaker_uuid"] == "uuid-B"
    another = restarted.create_project("Another")
    assert another.speaker_data["voices"]["B"] == voices["B"]


def test_explicit_restore_preserves_confirmed_japanese_attribution(tmp_path):
    service = _service(tmp_path)
    target = service.create_project("Older project")
    service.set_section(target, SectionName.P1, japanese="司会「発言」締め。", vietnamese="MC. Nhân vật.")
    service.save_project(target)
    passages = [item for item in segment_passages(target.sections).values() if item["language"] == "ja"]
    rows, _ = resolve_japanese_references([
        {"japanese_id": item["id"], "role": role, "confidence": "high", "warning": ""}
        for item, role in zip(passages, ("A", "B", "A"))], target.sections)
    target.speaker_data = confirmed_speaker_data(target, rows, target.speaker_data["identities"])
    target.speaker_data["voices"] = {"B": {"speaker_uuid": "old-B", "style_id": 77,
                                            "speaker": "Old", "style": "Old style"}}
    service.save_project(target)
    previous = deepcopy(target.speaker_data)

    source = service.create_project("Preset source")
    source.speaker_data["voices"] = _voices()
    service.save_default_role_voices(source)
    assert service.open_project(target.directory).speaker_data == previous
    service.restore_default_role_voices(target)
    reopened = service.open_project(target.directory)
    assert reopened.speaker_data["assignments"] == previous["assignments"]
    assert reopened.speaker_data["fingerprint"] == previous["fingerprint"]
    assert reopened.speaker_data["status"] == "confirmed"
    assert reopened.speaker_data["identities"] == previous["identities"]
    assert reopened.speaker_data["voices"]["B"]["speaker_uuid"] == "uuid-B"
    assert not speech_plan(reopened)[1]


def test_invalid_voice_ids_and_missing_role_do_not_overwrite_saved_preset(tmp_path):
    service = _service(tmp_path)
    state = service.create_project("Source")
    service.save_default_role_voices(state, _voices())
    original = deepcopy(service.default_role_voices())
    bad = _voices()
    bad["C"]["style_id"] = -1
    with pytest.raises(ValueError, match="ID"):
        service.save_default_role_voices(state, bad)
    bad = _voices()
    bad["D"]["speaker_uuid"] = ""
    with pytest.raises(ValueError, match="ID"):
        service.save_default_role_voices(state, bad)
    with pytest.raises(ValueError, match="vai D"):
        service.save_default_role_voices(state, {role: voice for role, voice in _voices().items() if role != "D"})
    assert service.default_role_voices() == original
    service.settings["default_role_voices"]["A"]["pitch"] = float("nan")
    with pytest.raises(ValueError, match="pitch"):
        service.create_project("Must not create")
    assert not (tmp_path / "projects" / "Must not create").exists()


def test_unavailable_style_is_reported_without_substitution(tmp_path):
    service = _service(tmp_path)
    state = service.create_project("Source")
    service.save_default_role_voices(state, _voices())
    project = service.create_project("Unavailable style")
    saved = deepcopy(project.speaker_data["voices"])
    catalog = [{"speaker_uuid": f"uuid-{role}", "styles": [{"id": index}]}
               for index, role in enumerate(ROLES, 1) if role != "C"]
    catalog.append({"speaker_uuid": "uuid-C", "styles": [{"id": 999}]})
    assert service.unavailable_role_voices(project.speaker_data["voices"], catalog) == ("C",)
    assert project.speaker_data["voices"] == saved
    assert service.default_role_voices()["C"]["style_id"] == 3
