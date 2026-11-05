import json

import pytest

from te.project import ProjectManager, SectionName
from te.script import ScriptService, parse_script


def fixture_text(count=43, vietnamese=False):
    sizes = [11, 11, 11, count-33]
    lines = []
    number = 0
    for section, size in zip(SectionName, sizes):
        lines.append(f'[{section}]\n')
        for _ in range(size):
            number += 1
            text = f'Câu {number}' if vietnamese else f'第{number}文。'
            lines.append(f'{number}. {text}\n')
    return ''.join(lines)


def test_import_43_edit_save_reopen_split_merge_reorder(tmp_path):
    ja = tmp_path / 'ja.txt'
    vi = tmp_path / 'vi.txt'
    ja.write_text(fixture_text(), encoding='utf-8')
    vi.write_text(fixture_text(vietnamese=True), encoding='utf-8')
    manager = ProjectManager()
    folder = tmp_path / 'project'
    project = manager.create(folder, '43 segments')
    service = ScriptService()
    result = service.import_files(project, ja, vi)
    assert len(result.segment_ids) == 43 and not result.warnings
    assert project.segments[42].vietnamese_reference == 'Câu 43'
    first_id = project.segments[0].id
    service.edit(project, first_id, japanese='日本語。', notes='key reveal', status='reviewed')
    manager.save(folder, project)
    project = manager.open(folder)
    assert project.segments[0].id == first_id
    assert project.segments[0].notes == 'key reveal'
    service.edit(project, first_id, clear_vietnamese=True)
    left, right = service.split(project, first_id, 2)
    assert len(project.segments) == 44
    assert left.id == first_id and left.japanese_text + right.japanese_text == '日本語。'
    merged = service.merge(project, left.id, right.id)
    assert merged.id == first_id and len(project.segments) == 43
    ids = [s.id for s in project.segments]
    ids[0], ids[1] = ids[1], ids[0]
    service.reorder(project, ids)
    manager.save(folder, project)
    reopened = manager.open(folder)
    assert reopened.segments[1].id == first_id
    assert [s.number for s in reopened.segments] == list(range(1, 44))
    assert len(reopened.segments) == 43
    assert service.warnings(reopened) == ['Japanese/Vietnamese count mismatch']


def test_mismatch_does_not_shift_alignment(tmp_path):
    ja, vi = tmp_path/'ja.txt', tmp_path/'vi.txt'
    ja.write_text(fixture_text(), encoding='utf-8')
    vi.write_text(fixture_text(42, True), encoding='utf-8')
    project = ProjectManager().create(tmp_path/'project', 'Story')
    result = ScriptService().import_files(project, ja, vi)
    assert result.warnings and all(s.vietnamese_reference is None for s in project.segments)


@pytest.mark.parametrize('text', [
    '[P1]\n1. One\n[P2]\n3. Three\n[P3]\n[P4]\n',
    '[P1]\n1. One\n[P3]\n2. Two\n[P4]\n',
    '1. No section\n[P1]\n[P2]\n[P3]\n[P4]\n',
])
def test_malformed_script_rejected(tmp_path, text):
    path = tmp_path/'bad.txt'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        parse_script(path)


def test_multiline_japanese_preserved(tmp_path):
    path = tmp_path/'script.txt'
    path.write_bytes(b'[P1]\r\n1.  \xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e  \r\n  \xe6\xac\xa1\xe3\x81\xae\xe8\xa1\x8c\r\n[P2]\r\n[P3]\r\n[P4]\r\n')
    parsed = parse_script(path)
    assert parsed[0].text == ' 日本語  \r\n  次の行'


def test_old_module0_project_loads_and_saves_as_v3(tmp_path):
    manager = ProjectManager()
    folder = tmp_path/'project'
    project = manager.create(folder, 'Old')
    manager.add_segment(project, SectionName.P1, 1, '旧テキスト')
    manager.save(folder, project)
    path = folder/'project.json'
    old = json.loads(path.read_text(encoding='utf-8'))
    old['schema_version'] = 1
    for segment in old['segments']:
        for key in ('notes','status','audio_ref','caption_refs','media_placement_refs'):
            segment.pop(key)
    path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
    opened = manager.open(folder)
    assert opened.segments[0].id == project.segments[0].id
    assert opened.segments[0].notes == '' and opened.segments[0].status == 'draft'
    manager.save(folder, opened)
    assert json.loads(path.read_text(encoding="utf-8"))['schema_version'] == 8


def test_add_move_delete_and_validation(tmp_path):
    manager = ProjectManager()
    project = manager.create(tmp_path/'project', 'Edit')
    service = ScriptService()
    a = service.add(project, SectionName.P1, '一。')
    b = service.add(project, SectionName.P2, '二。')
    c = service.add(project, SectionName.P4, '四。')
    service.move(project, c.id, SectionName.P3)
    assert [s.section for s in project.segments] == [SectionName.P1, SectionName.P2, SectionName.P3]
    with pytest.raises(ValueError, match='Section ordering'):
        service.reorder(project, [c.id, a.id, b.id])
    assert [s.id for s in project.segments] == [a.id, b.id, c.id]
    service.delete(project, b.id)
    assert [s.number for s in project.segments] == [1, 2]
    project.segments[1].id = a.id
    with pytest.raises(ValueError, match='duplicate'):
        project.validate()
