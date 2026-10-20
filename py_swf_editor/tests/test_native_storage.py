import pytest

from py_swf.cli import main
from py_swf.storage import write_swf
from tests.native_fixtures import as3_tag, swf_with


def test_native_cli_edit_exports_and_protects_input(tmp_path, capsys):
    original = swf_with([as3_tag()], 'CWS')
    path = tmp_path / 'original.swf'
    path.write_bytes(original)
    patch = tmp_path / 'source.as'
    patch.write_text('return score + 9;', encoding='utf-8')
    output = tmp_path / 'edited.swf'
    assert main(['edit-script', str(path), '--tag', '0', '--body', '1', '--source', str(patch), '--output', str(output)]) == 0
    assert 'native' in capsys.readouterr().out
    assert path.read_bytes() == original
    assert output.read_bytes() != original
    with pytest.raises(ValueError, match='original'):
        write_swf(path, output.read_bytes(), original=path)
    assert path.read_bytes() == original


def test_invalid_export_never_replaces_previous_output(tmp_path):
    target = tmp_path / 'saved.swf'
    original = swf_with([as3_tag()])
    target.write_bytes(original)
    with pytest.raises(ValueError, match='Invalid SWF'):
        write_swf(target, b'invalid', overwrite=True)
    assert target.read_bytes() == original
    with pytest.raises(FileExistsError):
        write_swf(target, original)
