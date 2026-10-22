"""Independent Flex source-path layout preserves generated namespace facts."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_parity_source_semantics import flex_source_files


def test_package_namespace_is_split_without_changing_method_or_class_namespace():
    body='localns function label():String { return "namespace fake = bogus;"; }'
    source='package app { public namespace localns = "urn:test"; public class Model { '+body+' } }'
    files=flex_source_files({'app/Model.as':source})
    assert set(files)=={'app/Model.as','app/localns.as'}
    assert body in files['app/Model.as']
    assert 'public namespace localns' not in files['app/Model.as']
    assert 'public namespace localns = "urn:test";' in files['app/localns.as']


def test_namespace_module_collision_is_rejected_instead_of_overwriting_class():
    source='package app { public namespace localns = "urn:test"; public class Model {} }'
    with pytest.raises(ValueError,match='collision'):
        flex_source_files({'app/Model.as':source,'app/localns.as':'package app { public class localns {} }'})
