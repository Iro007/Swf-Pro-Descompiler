"""Durable byte identities and observations; generated text is never executed."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

SCOPES = ('method', 'action', 'function', 'class', 'script', 'diagnostic')
OBSERVATION_SCOPES = (*SCOPES, 'outline', 'abc')
UNIT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS canonical_units (
 id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 document_id TEXT NOT NULL, scope TEXT NOT NULL, language TEXT,
 parent_unit_id TEXT, bytecode_sha256 TEXT, bytecode_bytes INTEGER,
 metadata_json TEXT NOT NULL, UNIQUE(artifact_id,document_id));
CREATE TABLE IF NOT EXISTS unit_observations (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 validation_id TEXT NOT NULL REFERENCES validations(id),
 unit_id TEXT REFERENCES canonical_units(id), document_id TEXT NOT NULL,
 scope TEXT NOT NULL, status TEXT NOT NULL, identity_verified INTEGER,
 output_sha256 TEXT, metadata_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS unit_observations_validation_idx ON unit_observations(validation_id);
CREATE INDEX IF NOT EXISTS unit_observations_unit_idx ON unit_observations(unit_id);
CREATE INDEX IF NOT EXISTS canonical_units_artifact_idx ON canonical_units(artifact_id);
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value.lower())


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


DERIVED_CANONICAL_FIELDS = frozenset(('code_origin','family_id','source_family','split','identity_status',
    'inventory_status','cross_validation','verification','read_only','source_edit_reason'))


def structural_identity(raw):
    return {key:value for key,value in raw.items() if key not in DERIVED_CANONICAL_FIELDS}


def import_inventory(connection, campaign, artifact, case):
    inventory = case.get('canonical_inventory') or {}
    if not isinstance(inventory, dict):
        raise ValueError('Canonical inventory must be an object')
    units = inventory.get('units', [])
    if not isinstance(units, list) or len(units) > 100000:
        raise ValueError('Invalid canonical unit count')
    mapping = {}
    for raw in units:
        if not isinstance(raw, dict):
            raise ValueError('Canonical unit must be an object')
        local = raw.get('document_id') or raw.get('local_id')
        scope = raw.get('scope')
        if not isinstance(local, str) or not local or scope not in SCOPES or local in mapping:
            raise ValueError('Duplicate or invalid canonical identity')
        input_hash = raw.get('input_sha256') or case.get('sha256')
        if input_hash != artifact or not valid_sha(input_hash):
            raise ValueError('Canonical input identity disagrees with artifact')
        identifier = raw.get('unit_id') or input_hash + ':' + local
        if identifier != input_hash + ':' + local:
            raise ValueError('Canonical unit is outside the input artifact')
        code_hash = raw.get('bytecode_sha256')
        if code_hash is not None and not valid_sha(code_hash):
            raise ValueError('Invalid canonical bytecode hash')
        size = integer(raw.get('bytecode_bytes'))
        previous = connection.execute('SELECT artifact_id,document_id,scope,bytecode_sha256,bytecode_bytes,metadata_json FROM canonical_units WHERE id=?', (identifier,)).fetchone()
        expected = (artifact, local, scope, code_hash, size)
        normalized={**raw,'unit_id':identifier,'input_sha256':input_hash,'document_id':local}
        previous_raw=json.loads(previous[5]) if previous is not None else None
        if previous_raw is not None:
            previous_raw={**previous_raw,'unit_id':identifier,'input_sha256':input_hash,'document_id':local}
        if previous is not None and (tuple(previous[:5]) != expected or structural_identity(previous_raw) != structural_identity(normalized)):
            raise ValueError('Canonical unit identity changed across campaigns')
        connection.execute('INSERT OR IGNORE INTO canonical_units VALUES (?,?,?,?,?,?,?,?,?)',
            (identifier, artifact, local, scope, raw.get('language'), raw.get('parent_unit_id'), code_hash, size, encoded(raw)))
        mapping[local] = raw | {'unit_id': identifier}
    if units:
        identity = digest({'campaign': campaign, 'artifact': artifact, 'kind': 'canonical_inventory'})
        payload = encoded(inventory).encode('utf-8')
        connection.execute('INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?)',
            (identity, campaign, str(case.get('relative') or artifact) + '/canonical_inventory',
             hashlib.sha256(payload).hexdigest(), 'canonical_inventory', encoded({'inventory': inventory})))
    return mapping


def import_observations(connection, campaign, validation, engine, mapping):
    observations = engine.get('unit_observations', engine.get('units', []))
    if not isinstance(observations, list) or len(observations) > 100000:
        raise ValueError('Invalid unit observation count')
    records = [(False, item) for item in observations]
    records.extend((True, item) for item in engine.get('documents', []) if isinstance(item, dict))
    by_unit_id={unit['unit_id']:unit for unit in mapping.values()}
    prepared=[]
    duplicate_keys={}
    for ordinal, (document, raw) in enumerate(records):
        if not isinstance(raw, dict):
            raise ValueError('Unit observation must be an object')
        local = raw.get('document_id') or raw.get('unit_id')
        if not isinstance(local, str) or not local:
            local = 'unidentified:' + str(ordinal)
        canonical = mapping.get(local)
        if canonical is None and raw.get('unit_id'):
            canonical = by_unit_id.get(raw['unit_id'])
        scope = raw.get('scope') or (canonical or {}).get('scope') or ('outline' if raw.get('generation') == 'outline_only' else 'script' if document and '/script:' in local else 'class' if document else 'method' if raw.get('language') in ('as3', 'avm2') else 'action')
        if scope not in OBSERVATION_SCOPES:
            raise ValueError('Invalid observation scope')
        verified = None
        if canonical is not None:
            verified = int(raw.get('identity_verified') is True and valid_sha(canonical.get('bytecode_sha256'))
                           and valid_sha(raw.get('bytecode_sha256')) and integer(canonical.get('bytecode_bytes')) is not None
                           and integer(raw.get('bytecode_bytes')) is not None
                           and raw.get('bytecode_sha256') == canonical.get('bytecode_sha256')
                           and raw.get('bytecode_bytes') == canonical.get('bytecode_bytes') and scope == canonical['scope'])
        metadata = dict(raw)
        if canonical is None:
            metadata['canonical_mapping'] = 'not_established'
        if local.startswith('unidentified:'):
            metadata['identity_missing'] = True
        output_hash = raw.get('output_sha256') or raw.get('source_sha256')
        if output_hash is not None and not valid_sha(output_hash):
            raise ValueError('Invalid output hash')
        status=str(raw.get('generation') or raw.get('status') or 'unknown')
        # A class/script document duplicated in the unit array is one physical
        # observation. Keep every document detail attached to that observation.
        key=(local,scope,raw.get('bytecode_sha256'),integer(raw.get('bytecode_bytes')),output_hash,status)
        if document and key in duplicate_keys and output_hash is not None:
            target=prepared[duplicate_keys[key]]
            target['metadata'].setdefault('document_observations',[]).append(dict(raw))
            if 'source' not in target['metadata'] and 'source' in raw:
                target['metadata']['source']=raw['source']
            continue
        identifier = digest({'validation': validation, 'ordinal': ordinal, 'document': document, 'local': local})
        duplicate_keys.setdefault(key,len(prepared))
        prepared.append({'identifier':identifier,'canonical':canonical,'local':local,'scope':scope,
                         'status':status,'verified':verified,'output_hash':output_hash,'metadata':metadata})
    for item in prepared:
        connection.execute('INSERT OR IGNORE INTO unit_observations VALUES (?,?,?,?,?,?,?,?,?,?)',
            (item['identifier'], campaign, validation, item['canonical']['unit_id'] if item['canonical'] else None,
             item['local'],item['scope'],item['status'],item['verified'],item['output_hash'],encoded(item['metadata'])))


def readonly(database):
    path = Path(database).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA trusted_schema=OFF')
    connection.execute('PRAGMA query_only=ON')
    return connection


def catalog_units(database, *, validation_id=None, campaign=None, unit_id=None, scope=None,
                  engine=None, offset=0, limit=50, include_metadata=False):
    if integer(offset) is None or integer(limit) is None or limit > 200 or not isinstance(include_metadata, bool):
        raise ValueError('Unit pagination requires offset >= 0 and 0 <= limit <= 200')
    if scope is not None and scope not in OBSERVATION_SCOPES:
        raise ValueError('Invalid unit scope')
    with closing(readonly(database)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='unit_observations'").fetchone():
            return {'items': [], 'total': 0, 'offset': offset, 'limit': limit, 'available': False}
        conditions, params = [], []
        for value, column in ((validation_id, 'o.validation_id'), (campaign, 'o.campaign_id'),
                              (unit_id, 'o.unit_id'), (scope, 'o.scope'), (engine, 't.id')):
            if value is not None:
                conditions.append(column + '=?'); params.append(str(value))
        base = ' FROM unit_observations o JOIN validations v ON v.id=o.validation_id JOIN builds b ON b.id=v.build_id JOIN tools t ON t.id=b.tool_id'
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        total = connection.execute('SELECT COUNT(*)' + base + where, params).fetchone()[0]
        rows = connection.execute('SELECT o.*,t.id engine' + base + where + ' ORDER BY o.campaign_id,o.validation_id,o.document_id,o.id LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        items = []
        for row in rows:
            item = {key: row[key] for key in row.keys() if key != 'metadata_json'}
            item['identity_verified'] = bool(item['identity_verified']) if item['identity_verified'] is not None else None
            if include_metadata:
                item['metadata'] = json.loads(row['metadata_json'])
                if row['unit_id']:
                    canonical = connection.execute('SELECT metadata_json FROM canonical_units WHERE id=?', (row['unit_id'],)).fetchone()
                    item['canonical'] = json.loads(canonical[0])
            items.append(item)
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
                'next_offset': offset + len(items) if offset + len(items) < total else None, 'available': True}


def catalog_evidence(database, *, campaign=None, evidence_id=None, kind=None, offset=0, limit=50, include_metadata=False):
    if integer(offset) is None or integer(limit) is None or limit > 200 or not isinstance(include_metadata, bool):
        raise ValueError('Evidence pagination requires offset >= 0 and 0 <= limit <= 200')
    with closing(readonly(database)) as connection:
        conditions, params = [], []
        for value, column in ((campaign, 'campaign_id'), (evidence_id, 'id'), (kind, 'kind')):
            if value is not None:
                conditions.append(column + '=?'); params.append(str(value))
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        total = connection.execute('SELECT COUNT(*) FROM evidence' + where, params).fetchone()[0]
        rows = connection.execute('SELECT * FROM evidence' + where + ' ORDER BY campaign_id,id LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        items = []
        for row in rows:
            item = {key: row[key] for key in row.keys() if key != 'metadata_json'}
            if include_metadata:
                item['metadata'] = json.loads(row['metadata_json'])
            items.append(item)
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
                'next_offset': offset + len(items) if offset + len(items) < total else None, 'available': True}
