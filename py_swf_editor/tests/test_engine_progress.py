"""Progress cannot confuse expanded inventory or recovered mapping with source gains."""
from copy import deepcopy

import pytest

from tools.compare_engine_progress import compare


def unit(name, source, *, verified=True, digest='b'):
    return {'document_id': name, 'language': 'as2', 'bytecode_sha256': digest * 64,
            'bytecode_bytes': 3, 'tag_path': [2], 'generation': 'available' if source else 'pcode',
            'identity_verified': verified}


def report(units, *, source=None, status='output_unmapped', digest='a'):
    return {'cases': [{'relative': 'fixture.swf', 'sha256': digest * 64, 'engines': [
        {'engine': 'rust', 'status': status, 'quality': {'source_available': source, 'expected_units_reported': len(units)},
         'units': units, 'hot': {'median_ms': 10}, 'failures': []}]}]}


def test_new_nested_units_and_mapping_recovery_are_not_algorithm_source_gains():
    before = report([unit('parent', True), unit('missing', False, verified=False)])
    after = report([unit('parent', True), unit('missing', True), unit('child', False)], source=2, status='measured')
    row = compare(before, after, 'test')[0]
    assert row['reported_source_before'] is None
    assert row['common_verified_units'] == row['source_on_common_before'] == row['source_on_common_after'] == 1
    assert row['new_inventory_units'] == row['recovered_identity_units'] == 1
    assert row['new_inventory_source'] == 0 and row['gained_source_units'] == []


def test_real_common_source_gain_and_withdrawal_preserve_unit_hashes():
    row = compare(report([unit('gain', False), unit('withdraw', True)]),
                  report([unit('gain', True), unit('withdraw', False)]), 'test')[0]
    assert row['gained_source_units'][0]['id'] == 'gain'
    assert row['lost_source_units'][0]['id'] == 'withdraw'
    assert row['gained_source_units'][0]['bytecode_sha256'] == 'b' * 64


def test_changed_inputs_or_duplicate_units_reject_comparison():
    before = report([unit('one', True)])
    with pytest.raises(ValueError, match='input hashes'):
        compare(before, report([unit('one', True)], digest='c'), 'test')
    duplicate = report([unit('one', True), unit('one', True)])
    with pytest.raises(ValueError, match='Duplicate'):
        compare(duplicate, duplicate, 'test')


def test_changed_bytecode_with_same_name_is_discovery_not_source_gain():
    row = compare(report([unit('one', False)]), report([unit('one', True, digest='c')]), 'test')[0]
    assert row['common_verified_units'] == 0 and not row['gained_source_units']
    assert row['new_inventory_units'] == row['removed_inventory_units'] == 1
