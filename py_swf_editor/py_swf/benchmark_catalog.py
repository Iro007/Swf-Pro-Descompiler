"""Read-only imports of benchmark evidence into an independent SQLite catalog.

No imported command, script, binary or source text is executed. Measurements from
different campaigns, builds, operations and phases never form a pooled median.
Exit code zero records execution success only, never source or behavior parity.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import statistics
import tempfile
import zipfile

MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_BUNDLE_ENTRIES = 20000
MAX_RECORDS = 100000
SCHEMA_VERSION = 2
CLAIMS = {"parity": False, "superiority": False, "performance_winner": None}
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS environments (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, platform TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tools (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS builds (
 id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), version TEXT,
 fingerprint TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, bytes INTEGER, language TEXT, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, environment_id TEXT NOT NULL REFERENCES environments(id),
 operation TEXT NOT NULL, created_at TEXT, source_kind TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), path TEXT NOT NULL,
 sha256 TEXT NOT NULL, kind TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_artifacts (
 campaign_id TEXT NOT NULL REFERENCES campaigns(id), artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 PRIMARY KEY (campaign_id,artifact_id));
CREATE TABLE IF NOT EXISTS validations (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 artifact_id TEXT NOT NULL REFERENCES artifacts(id), build_id TEXT NOT NULL REFERENCES builds(id),
 file TEXT NOT NULL, operation TEXT NOT NULL, status TEXT NOT NULL,
 expected_units INTEGER, source_available INTEGER, coverage_percent REAL,
 compilation TEXT NOT NULL, behavior TEXT NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 validation_id TEXT NOT NULL REFERENCES validations(id), ordinal INTEGER NOT NULL,
 phase TEXT NOT NULL, status TEXT NOT NULL, success INTEGER, elapsed_ms REAL,
 peak_tree_rss_bytes INTEGER, metadata_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_campaign_idx ON runs(campaign_id);
CREATE INDEX IF NOT EXISTS validations_campaign_idx ON validations(campaign_id);
"""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _json(value).encode("utf-8")).hexdigest()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _integer(value):
    value = _number(value)
    return int(value) if value is not None and value.is_integer() else None


def _sha(value):
    return str(value).lower() if value and _SHA.fullmatch(str(value)) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _pick(record, *names, default=None):
    for name in names:
        if record.get(name) is not None:
            return record[name]
    return default


def _canonical_engine(value):
    return {"ffdec": "jpexs"}.get(str(value), str(value))


def _canonical_operation(value):
    return {"decompile_as": "source"}.get(str(value), str(value))


def _load_json(data):
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("Metadata exceeds the 64 MiB limit")
    return json.loads(data.decode("utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number: " + value)))


def _connect(database):
    path = Path(database).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    version = None
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_meta'").fetchone():
        version = connection.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
    if version and version[0] not in ('1', str(SCHEMA_VERSION)):
        connection.close()
        raise ValueError("Unsupported benchmark catalog schema")
    from .benchmark_unit_catalog import UNIT_SCHEMA
    connection.executescript(_SCHEMA + UNIT_SCHEMA)
    connection.execute("INSERT OR REPLACE INTO catalog_meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    connection.commit()
    return connection, path


def _environment(connection, metadata, source_kind):
    metadata = _dict(metadata)
    platform = str(_pick(metadata, "platform", "os", "system", default="unknown"))
    label = str(_pick(metadata, "label", "name", "environment", default=("cloud / " if source_kind == "cloud_bundle" else "local / ") + platform))
    identifier = _digest({"source_kind": source_kind, "metadata": metadata})
    connection.execute("INSERT OR IGNORE INTO environments VALUES (?,?,?,?)", (identifier, label, platform, _json(metadata)))
    return identifier


def _tool(connection, engine, descriptor, provenance, environment_id):
    engine = str(engine)
    descriptor, provenance = _dict(descriptor), _dict(provenance)
    defaults = {"native": "PySWF", "rust": "RustSWF", "jpexs": "JPEXS", "pyflashkit": "pyflashkit"}
    name = str(descriptor.get("name") or defaults.get(engine, engine))
    # Native Python here is the local PySWF implementation, never pyflashkit.
    if engine == "native":
        name = "PySWF"
    connection.execute("INSERT OR IGNORE INTO tools VALUES (?,?,?)", (engine, name, _json(descriptor)))
    version = _pick(descriptor, "version", "tool_version", default=provenance.get("version"))
    identity = {"engine": engine, "descriptor": descriptor, "provenance": provenance, "environment_id": environment_id}
    identifier = _digest(identity)
    connection.execute("INSERT OR IGNORE INTO builds VALUES (?,?,?,?,?)", (identifier, engine, str(version) if version is not None else None, identifier, _json(identity)))
    return identifier


def _artifact(connection, record, fallback):
    sha = _sha(_pick(record, "sha256", "artifact_sha256", "input_sha256", "swf_sha256", "file_sha256"))
    identifier = sha or "unidentified:" + _digest(fallback)
    language = _pick(record, "language", "avm", "avm_version")
    language = {"as2": "AVM1", "as3": "AVM2", "avm1": "AVM1", "avm2": "AVM2"}.get(str(language).lower(), language)
    connection.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?)", (identifier, sha, _integer(_pick(record, "bytes", "size", "size_bytes", "input_bytes")), language, _json(record)))
    connection.execute("UPDATE artifacts SET language=COALESCE(language,?),bytes=COALESCE(bytes,?) WHERE id=?", (language, _integer(_pick(record, "bytes", "size", "size_bytes", "input_bytes")), identifier))
    return identifier


def _compilation(value):
    if not value:
        return "not_measured"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        states = [str(v.get("compilation", "not_run")) for v in value if isinstance(v, dict)]
        if not states:
            return "not_measured"
        if "failed" in states:
            return "sample_failed"
        if "passed" in states:
            return "sample_passed"
    return "not_measured"


def _quality(record, operation):
    quality = _dict(record.get("quality"))
    expected = _integer(_pick(quality, "expected_units_reported", "expected_units", default=record.get("expected_units")))
    available = _integer(_pick(quality, "source_available", default=record.get("source_available")))
    mapped = quality.get("unit_mapping") in ("canonical", "verified", "matched") or record.get("coverage_verified") is True
    if record.get('inventory_status') == 'disagreement':
        mapped = False
    coverage = 100 * available / expected if operation == "source" and mapped and expected and available is not None and available <= expected else None
    if not mapped or operation != "source":
        available = None
    compilation = _compilation(_pick(record, "independent_compilation_status", "recompilation_status", "compilation", "compilation_sample"))
    behavior = str(_pick(record, "behavior", "behavior_status", "semantic_status", default="not_measured"))
    if behavior == "passed" and not record.get("behavior_evidence"):
        behavior = "reported_passed_unverified"
    return expected, available, coverage, compilation, behavior


def _validation(connection, campaign, artifact, build, filename, operation, record, identity):
    identifier = _digest({"campaign": campaign, "observation": identity})
    quality = _quality(record, operation)
    connection.execute("INSERT OR IGNORE INTO campaign_artifacts VALUES (?,?)", (campaign, artifact))
    connection.execute("INSERT OR IGNORE INTO validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, campaign, artifact, build, str(filename), operation, str(record.get("status") or "indeterminate"), *quality, _json(record)))
    return identifier


def _boolean(value):
    if value is True or value is False:
        return value
    if isinstance(value, str):
        return {"true": True, "1": True, "false": False, "0": False}.get(value.strip().lower())
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _exit_success(sample):
    value = _pick(sample, "exit_code", "returncode", "return_code")
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number == 0 if math.isfinite(number) and number.is_integer() else None


def _sample_status(sample):
    status = str(_pick(sample, "status", "execution_status", default=""))
    diagnostic = str(_pick(sample, "diagnostic", "error", "stderr", default="")).lower()
    termination = str(sample.get("termination") or "").lower()
    if _boolean(sample.get("timeout")) is True or _boolean(sample.get("timed_out")) is True or "timeout" in status.lower() or "timeout" in termination or "timed out" in diagnostic:
        return "timeout", 0
    flags = [_boolean(sample.get("ok")), _boolean(sample.get("success")), _exit_success(sample)]
    declared_status = True if status.lower() in ("success", "ok", "passed", "completed") else False if status.lower() in ("error", "failed", "crashed") else None
    flags.append(declared_status)
    observed = {flag for flag in flags if flag is not None}
    if len(observed) > 1:
        return "indeterminate_conflicting_execution_flags", None
    if observed:
        success = observed.pop()
        return status or ("success" if success else "error"), int(success)
    return status or "indeterminate", None


def _run(connection, campaign, validation, ordinal, phase, sample):
    status, success = _sample_status(sample)
    sample = dict(sample)
    if status == "indeterminate_conflicting_execution_flags":
        sample["catalog_execution_diagnostic"] = "Conflicting execution flags or exit code; execution result is indeterminate"
    elapsed = _number(_pick(sample, "total_with_startup_ms" if phase == "cold" else "wall_ms", "wall_ms", "elapsed_ms", "duration_ms", "wall_time_ms", "time_ms"))
    if elapsed is None:
        seconds = _number(_pick(sample, "wall_seconds", "elapsed_seconds", "duration_seconds", "wall_s"))
        elapsed = seconds * 1000 if seconds is not None else None
    peak = _integer(_pick(sample, "peak_tree_rss_bytes", "peak_rss_bytes", "rss_peak_bytes", "peak_rss"))
    if peak is None:
        sampled_mib = _number(sample.get("sampled_peak_tree_rss_mib"))
        peak = _integer(sampled_mib * 1048576) if sampled_mib is not None else None
    if _integer(_pick(sample, "memory_samples", "rss_samples")) == 0:
        peak = None
    identifier = _digest({"campaign": campaign, "validation": validation, "phase": phase, "ordinal": ordinal})
    connection.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, campaign, validation, ordinal, phase, status, success, elapsed, peak, _json(sample)))


def _evidence(connection, campaign, path, data, kind, metadata=None):
    digest = _digest(data)
    identifier = _digest({"campaign": campaign, "path": path, "sha256": digest})
    connection.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?)", (identifier, campaign, str(path), digest, kind, _json(metadata or {"bytes": len(data)})))


def _result(connection, campaign, imported, digest, warnings):
    return {"campaign_id": campaign, "imported": imported, "source_sha256": digest,
            "run_count": connection.execute("SELECT COUNT(*) FROM runs WHERE campaign_id=?", (campaign,)).fetchone()[0],
            "artifact_count": connection.execute("SELECT COUNT(*) FROM campaign_artifacts ca JOIN artifacts a ON a.id=ca.artifact_id WHERE ca.campaign_id=? AND a.sha256 IS NOT NULL", (campaign,)).fetchone()[0],
            "unidentified_artifact_groups": connection.execute("SELECT COUNT(*) FROM campaign_artifacts ca JOIN artifacts a ON a.id=ca.artifact_id WHERE ca.campaign_id=? AND a.sha256 IS NULL", (campaign,)).fetchone()[0],
            "warnings": warnings}


def import_local_report(database, path):
    """Import schema-1 report samples exactly once, preserving every repetition."""
    path = Path(path)
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("Metadata exceeds the 64 MiB limit")
    data = path.read_bytes()
    report = _load_json(data)
    if not isinstance(report, dict) or report.get("schema_version") != 1 or not isinstance(report.get("cases"), list):
        raise ValueError("Expected a schema-1 benchmark report with cases")
    campaign, digest = _digest({"source_kind": "local_report", "report": report}), _digest(data)
    warnings = ["Execution success does not establish source or behavior equivalence", "Sampled peak RSS is not an exact peak"]
    if report.get("provenance", {}).get("implementation_unchanged") is False:
        warnings.append("Implementation changed during this campaign")
    connection, _ = _connect(database)
    try:
        with connection:
            already_present = connection.execute("SELECT environment_id FROM campaigns WHERE id=?", (campaign,)).fetchone()
            environment = already_present[0] if already_present else _environment(connection, report.get("environment"), "local_report")
            operation = str(report.get("operation") or "source")
            connection.execute("INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?,?,?,?)", (campaign, str(report.get("label") or path.parent.name), environment, operation, report.get("created_at"), "local_report", digest, _json({k: v for k, v in report.items() if k != "cases"})))
            _evidence(connection, campaign, path.name, data, "local_report")
            descriptors = {str(d.get("id")): d for d in report.get("engine_catalog", []) if isinstance(d, dict)}
            from .benchmark_unit_catalog import import_inventory, import_observations
            for index, case in enumerate(report["cases"]):
                if not isinstance(case, dict):
                    raise ValueError("Invalid benchmark case")
                artifact = _artifact(connection, {k: v for k, v in case.items() if k not in ("engines","canonical_inventory")}, {"campaign": campaign, "case": index})
                canonical = import_inventory(connection, campaign, artifact, case)
                filename = _pick(case, "relative", "file", "name", default="unidentified")
                for engine_index, engine in enumerate(case.get("engines", [])):
                    engine_id = str(engine.get("engine") or "unknown")
                    descriptor = {**descriptors.get(engine_id, {}), **_dict(engine.get("tool_fingerprint"))}
                    provenance = _dict(_dict(_dict(report.get("provenance")).get("references")).get(engine_id))
                    if engine_id == "native":
                        provenance = {"implementation_sha256": _dict(report.get("provenance")).get("implementation_sha256_before")}
                    build = _tool(connection, engine_id, descriptor, provenance, environment)
                    engine_operation = str(engine.get("operation") or operation)
                    metadata = {k: v for k, v in engine.items() if k not in ("cold_samples", "hot_samples", "warmup_samples", "units", "documents", "unit_observations")}
                    metadata["comparison"] = case.get("comparison", {})
                    validation = _validation(connection, campaign, artifact, build, filename, engine_operation, metadata, [index, engine_index])
                    import_observations(connection, campaign, validation, engine, canonical)
                    for phase in ("cold", "hot", "warmup"):
                        for ordinal, sample in enumerate(engine.get(phase + "_samples", [])):
                            _run(connection, campaign, validation, ordinal, phase, sample)
            return _result(connection, campaign, not bool(already_present), digest, warnings)
    finally:
        connection.close()


def _safe_zip(path):
    """Read bounded regular files without extracting any archive path."""
    if Path(path).stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Cloud bundle exceeds size limit")
    members, total = {}, 0
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > MAX_BUNDLE_ENTRIES:
            raise ValueError("Too many cloud bundle entries")
        for item in archive.infolist():
            name = item.filename.replace("\\", "/")
            parsed = PurePosixPath(name)
            mode = item.external_attr >> 16
            if parsed.is_absolute() or ".." in parsed.parts or ":" in name or "\x00" in name or stat.S_ISLNK(mode):
                raise ValueError("Unsafe cloud bundle member: " + name)
            if item.is_dir():
                continue
            if name in members:
                raise ValueError("Duplicate cloud bundle member: " + name)
            total += item.file_size
            if total > MAX_BUNDLE_BYTES or item.file_size > MAX_METADATA_BYTES:
                raise ValueError("Cloud bundle member or expanded size exceeds limit")
            members[name] = archive.read(item)
    return members


def _manifest_hashes(value):
    """Yield explicit file hashes from common manifest forms, never guess them."""
    if isinstance(value, dict):
        path = _pick(value, "path", "file", "relative", "filename")
        digest = _sha(_pick(value, "sha256", "hash", "file_sha256"))
        if path and digest:
            yield str(path).replace("\\", "/"), digest
        for key, child in value.items():
            if isinstance(child, str) and _sha(child) and ("/" in key or "." in key):
                yield key.replace("\\", "/"), child.lower()
            elif isinstance(child, (dict, list)):
                yield from _manifest_hashes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _manifest_hashes(child)


def _verify_manifests(members):
    verified, warnings = 0, []
    for name, data in members.items():
        if PurePosixPath(name).name.lower() not in ("manifest.json", "bundle-manifest.json", "integrity.json", "manifest.sha256", "sha256sums", "sha256sums.txt"):
            continue
        if name.lower().endswith(".json"):
            pairs = list(_manifest_hashes(_load_json(data)))
        else:
            pairs = [(parts[1].lstrip("*"), parts[0]) for line in data.decode("utf-8-sig").splitlines() if len(parts := line.split(maxsplit=1)) == 2 and _sha(parts[0])]
        parent = PurePosixPath(name).parent
        for relative, expected in pairs:
            candidates = (relative, str(parent / relative))
            found = next((candidate for candidate in candidates if candidate in members), None)
            # Corpus paths may be absolute paths of the producing cloud host.
            if found is None:
                matches = [key for key in members if relative.endswith("/" + key) or key.endswith("/" + relative)]
                found = matches[0] if len(matches) == 1 else None
            if found is None:
                if PurePosixPath(name).name.lower() == "integrity.json":
                    raise ValueError("Integrity manifest payload is absent: " + relative)
                warnings.append("Manifest artifact is absent from bundle: " + relative)
                continue
            if _digest(members[found]) != expected:
                raise ValueError("Manifest SHA-256 mismatch: " + found)
            verified += 1
    if not verified:
        warnings.append("No included payload hash was verified by a manifest")
    return verified, warnings


def _records(data, suffix):
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("Metadata exceeds the 64 MiB limit")
    if suffix == ".jsonl":
        rows = [_load_json(line) for line in data.splitlines() if line.strip()]
    elif suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    else:
        value = _load_json(data)
        rows = value if isinstance(value, list) else _pick(_dict(value), "runs", "results", "records", default=[])
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid or excessive cloud run records")
    return rows


def _cloud_records(members):
    preferred = [name for name in members if name.endswith("runs.jsonl")]
    if preferred:
        return [(name, _records(members[name], ".jsonl")) for name in sorted(preferred)]
    preferred = [name for name in members if PurePosixPath(name).name in ("runs.json", "runs.csv", "raw_runs.csv")]
    if preferred:
        name = sorted(preferred, key=lambda n: (not n.endswith(".json"), n))[0]
        return [(name, _records(members[name], PurePosixPath(name).suffix))]
    databases = [name for name in members if PurePosixPath(name).suffix in (".sqlite", ".sqlite3", ".db")]
    if databases:
        with tempfile.TemporaryDirectory(prefix="flash-catalog-read-") as folder:
            path = Path(folder) / "evidence.sqlite"
            path.write_bytes(members[databases[0]])
            db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA trusted_schema=OFF")
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if "runs" not in tables:
                    raise ValueError("Cloud database has no runs table")
                rows = [dict(row) for row in db.execute("SELECT * FROM runs LIMIT ?", (MAX_RECORDS + 1,))]
                if len(rows) > MAX_RECORDS:
                    raise ValueError("Excessive cloud run records")
                return [(databases[0], rows)]
            finally:
                db.close()
    raise ValueError("Cloud bundle contains no supported run evidence")


def import_cloud_bundle(database, path, expected_sha256=None):
    """Import bounded ZIP evidence and verify the expected bundle and manifest hashes."""
    path = Path(path)
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Cloud bundle exceeds size limit")
    digest = _digest(path.read_bytes())
    if expected_sha256 is not None and (not _sha(expected_sha256) or digest != str(expected_sha256).lower()):
        raise ValueError("Cloud bundle SHA-256 mismatch")
    members = _safe_zip(path)
    verified, warnings = _verify_manifests(members)
    warnings += ["Cloud/Linux measurements remain separate from local/Windows campaigns", "Execution success does not establish source or behavior equivalence", "Sampled peak RSS is not an exact peak"]
    files = _cloud_records(members)
    if sum(len(rows) for _, rows in files) > MAX_RECORDS:
        raise ValueError("Excessive cloud run records across bundle")
    # Bundle content identity survives ZIP timestamp and compression changes.
    campaign = _digest({"source_kind": "cloud_bundle", "members": {name: _digest(data) for name, data in sorted(members.items())}})
    manifests = {name: _load_json(data) for name, data in members.items() if PurePosixPath(name).name == "manifest.json"}
    environment = next((_load_json(data) for name, data in members.items() if PurePosixPath(name).name in ("environment.json", "environment_snapshot.json", "host.json")), {})
    if not isinstance(environment, dict):
        environment = {"recorded": environment}
    environment.setdefault("platform", "Linux (cloud; recorded host details unavailable)")
    environment.setdefault("label", "Codex cloud / Linux")
    artifacts = []
    for manifest in manifests.values():
        for key in ("artifacts", "files", "corpus"):
            items = _dict(manifest).get(key, [])
            if isinstance(items, list):
                artifacts.extend(item for item in items if isinstance(item, dict))
    artifact_map, basename_map, ambiguous_basenames = {}, {}, set()
    for artifact in artifacts:
        for key in ("path", "file", "relative", "name", "id", "sha256"):
            if artifact.get(key):
                artifact_map[str(artifact[key])] = artifact
                basename = PurePosixPath(str(artifact[key])).name
                if basename in basename_map and basename_map[basename] != artifact:
                    ambiguous_basenames.add(basename)
                basename_map[basename] = artifact
    basename_map = {name: record for name, record in basename_map.items() if name not in ambiguous_basenames}
    tools = {}
    for name, data in members.items():
        if PurePosixPath(name).name not in ("tools.json", "tool_versions.json", "toolchain.json", "tool_manifest.json") and not ("tools" in PurePosixPath(name).parts and name.endswith(".json")):
            continue
        value = _load_json(data)
        if isinstance(value, dict) and value.get("id"):
            tools[_canonical_engine(value["id"])] = {**value, "raw_tool_id": value["id"]}
            continue
        values = _dict(value).get("tools", value)
        if isinstance(values, list):
            tools.update({_canonical_engine(_pick(item, "id", "tool", "name")): item for item in values if isinstance(item, dict)})
        elif isinstance(values, dict):
            tools.update({_canonical_engine(key): value if isinstance(value, dict) else {"version": value} for key, value in values.items()})
    connection, _ = _connect(database)
    try:
        with connection:
            if connection.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign,)).fetchone():
                return _result(connection, campaign, False, digest, warnings)
            environment_id = _environment(connection, environment, "cloud_bundle")
            operations = {_canonical_operation(_pick(row, "operation", "mode", "action", default="indeterminate")) for _, rows in files for row in rows}
            operation = next(iter(operations)) if len(operations) == 1 else "mixed"
            created_at = next((_pick(value, "created_at", "timestamp", "generated_at") for value in manifests.values() if _pick(_dict(value), "created_at", "timestamp", "generated_at")), None)
            connection.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?)", (campaign, path.stem, environment_id, operation, created_at, "cloud_bundle", digest, _json({"manifest_hashes_verified": verified, "warnings": warnings, "members": list(members)})))
            for index, artifact in enumerate(artifacts):
                identifier = _artifact(connection, artifact, {"campaign": campaign, "manifest_artifact": index})
                connection.execute("INSERT OR IGNORE INTO campaign_artifacts VALUES (?,?)", (campaign, identifier))
            for engine, descriptor in tools.items():
                _tool(connection, engine, descriptor, {}, environment_id)
            for name, data in members.items():
                _evidence(connection, campaign, name, data, PurePosixPath(name).suffix.lstrip(".") or "binary")
            observations = {}
            for source, rows in files:
                for index, raw in enumerate(rows):
                    raw_engine = str(_pick(raw, "tool", "engine", "tool_id", "decompiler", default="unknown"))
                    engine = _canonical_engine(raw_engine)
                    artifact_identity = str(raw.get("artifact_id") or "")
                    manifest_artifact = artifact_map.get(artifact_identity, {})
                    filename = str(_pick(manifest_artifact, "path", "file", "relative", "name", default=_pick(raw, "file", "input", "artifact", "swf", "input_path", "path", default=artifact_identity or "unidentified")))
                    descriptor = {**tools.get(engine, {}), **{key: raw[key] for key in ("version", "tool_version", "tool_sha256", "build_sha256") if raw.get(key) is not None}}
                    build = _tool(connection, engine, descriptor, {}, environment_id)
                    recorded_sha = _sha(_pick(raw, "artifact_sha256", "input_sha256", "swf_sha256", "sha256"))
                    artifact_record = {**artifact_map.get(recorded_sha, manifest_artifact or artifact_map.get(filename, basename_map.get(PurePosixPath(filename).name, {}))), **{key: raw[key] for key in ("sha256", "artifact_sha256", "input_sha256", "swf_sha256", "bytes", "input_bytes", "language", "avm") if raw.get(key) is not None}}
                    artifact = _artifact(connection, artifact_record, {"campaign": campaign, "file": filename})
                    raw_operation = str(_pick(raw, "operation", "mode", "action", default="indeterminate"))
                    op = _canonical_operation(raw_operation)
                    phase = str(_pick(raw, "phase", "temperature", default="heap_probe" if "heap" in source else "cold"))
                    key = (artifact, build, filename, op, source)
                    if key not in observations:
                        metadata = {"status": "execution_only", "raw_tool_id": raw_engine, "raw_operation": raw_operation, "coverage_verified": False, "independent_compilation_status": "not_measured", "source": source, "input_artifact": artifact_record, "timing_scope": raw.get("timing_scope", "fresh external process; includes startup"), "memory_scope": "sampled process-tree RSS"}
                        metadata.update({field: raw[field] for field in ("quality", "expected_units", "source_available", "coverage_verified", "independent_compilation_status", "behavior_status", "semantic_status", "recompilation_status", "valid_performance_summary", "category", "benchmark_role", "process_mode") if field in raw})
                        observations[key] = _validation(connection, campaign, artifact, build, filename, op, metadata, key)
                    _run(connection, campaign, observations[key], index, phase, raw)
            for name, data in members.items():
                if PurePosixPath(name).name == "internal_reimport.json":
                    value = _load_json(data)
                    _evidence(connection, campaign, name, data, "internal_reimport", {"result": value, "independent_compilation": "not_measured", "parity": False})
            return _result(connection, campaign, True, digest, warnings)
    finally:
        connection.close()


def _median(values):
    return statistics.median(values) if values else None


def _new_phase_summary():
    return {'run_count':0,'success_count':0,'error_count':0,'timeout_count':0,'indeterminate_count':0,
            'censored_count':0,'peak_tree_rss_bytes':None,'rss_sample_count':0,'rusage_maxrss_kib':None,
            'source_output_file_count':0,'source_output_bytes':0,'code_output_file_count':0,
            'exit_zero_count':0,'as_error_marker_count':0,
            '_success_ms':[],'_error_ms':[],'_timeout_ms':[],'_censored_ms':[],
            '_states':set(),'_rss_intervals':set(),'_adapter_statuses':set()}


def _finish_phase_summary(summary, observation_status='indeterminate'):
    states=summary.pop('_states')
    summary['status']=next(iter(states)) if len(states)==1 else 'mixed' if states else observation_status
    summary['_conflicting_execution_flags']='indeterminate_conflicting_execution_flags' in states
    for field,values in (('median_ms','_success_ms'),('median_error_ms','_error_ms'),
                         ('median_timeout_ms','_timeout_ms'),('median_censored_ms','_censored_ms')):
        summary[field]=_median(summary.pop(values))
    summary['rss_sample_interval_s']=sorted(summary.pop('_rss_intervals'))
    summary['adapter_statuses']=[_load_json(item.encode()) for item in sorted(summary.pop('_adapter_statuses'))]
    return summary


def _catalog_run_summaries(connection, validation_ids):
    """Read selected runs once and retain counters/median values, never raw JSON.

    The validation column need not have an index in older read-only catalogs.
    Bound parameters are batched only for a host's SQLite variable limit; the
    normal 5001-observation bound fits one query on supported SQLite builds.
    """
    phases={}
    variable_limit=max(1,connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
    for offset in range(0,len(validation_ids),variable_limit):
        selected=validation_ids[offset:offset+variable_limit]
        placeholders=','.join('?' for _ in selected)
        query=('SELECT validation_id,phase,status,success,elapsed_ms,peak_tree_rss_bytes,metadata_json '
               'FROM runs WHERE validation_id IN ('+placeholders+')')
        for sample in connection.execute(query,selected):
            key=(sample['validation_id'],sample['phase'])
            if key not in phases:phases[key]=_new_phase_summary()
            summary=phases[key]
            raw=json.loads(sample['metadata_json'])
            success,status,elapsed=sample['success'],sample['status'],sample['elapsed_ms']
            censored=_boolean(raw.get('wall_is_censored')) is True
            summary['run_count']+=1
            summary['success_count']+=success==1
            summary['indeterminate_count']+=success is None
            summary['_states'].add(status)
            if success==1 and not censored and elapsed is not None:summary['_success_ms'].append(elapsed)
            if status=='timeout':
                summary['timeout_count']+=1
                if elapsed is not None:summary['_timeout_ms'].append(elapsed)
            if success==0 and status!='timeout':
                summary['error_count']+=1
                if elapsed is not None:summary['_error_ms'].append(elapsed)
            summary['censored_count']+=censored
            if censored and elapsed is not None:summary['_censored_ms'].append(elapsed)
            peak=sample['peak_tree_rss_bytes']
            if peak is not None:
                summary['peak_tree_rss_bytes']=peak if summary['peak_tree_rss_bytes'] is None else max(summary['peak_tree_rss_bytes'],peak)
            summary['rss_sample_count']+=_integer(_pick(raw,'rss_samples','memory_samples')) or 0
            interval=_number(raw.get('rss_sample_interval_s'))
            if interval is not None:summary['_rss_intervals'].add(interval)
            usage=_integer(raw.get('rusage_maxrss_kib'))
            if usage is not None:
                summary['rusage_maxrss_kib']=usage if summary['rusage_maxrss_kib'] is None else max(summary['rusage_maxrss_kib'],usage)
            if raw.get('adapter_status') is not None:summary['_adapter_statuses'].add(_json(raw['adapter_status']))
            for field,raw_field in (('source_output_file_count','as_file_count'),('source_output_bytes','as_bytes'),('code_output_file_count','code_output_file_count')):
                summary[field]=max(summary[field],_integer(raw.get(raw_field)) or 0)
            summary['exit_zero_count']+=_exit_success(raw) is True
            summary['as_error_marker_count']+=_integer(raw.get('as_error_marker_count')) or 0
    return {key:_finish_phase_summary(summary) for key,summary in phases.items()}


def catalog_summary(database, **filters):
    """Return bounded source-backed rows, retaining all measurement boundaries."""
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("Benchmark catalog does not exist: " + str(path))
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    try:
        clauses, args = [], []
        for name, column in (("campaign", "c.id"), ("operation", "v.operation"), ("engine", "t.id")):
            if filters.get(name) is not None:
                clauses.append(column + "=?")
                args.append(str(filters[name]))
        if filters.get("environment") is not None:
            clauses.append("(e.id=? OR e.label=? OR e.platform=?)")
            args.extend([str(filters["environment"])] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit = min(5000, max(0, int(filters.get("limit", 5000))))
        query = """SELECT v.*, a.sha256,a.language,b.version,b.metadata_json build_metadata_json,t.id engine,t.name engine_name,
                          e.label environment,e.platform,e.id environment_id
                   FROM validations v JOIN campaigns c ON c.id=v.campaign_id
                   JOIN artifacts a ON a.id=v.artifact_id JOIN builds b ON b.id=v.build_id
                   JOIN tools t ON t.id=b.tool_id JOIN environments e ON e.id=c.environment_id"""
        observations = connection.execute(query + where + " ORDER BY c.created_at DESC,v.campaign_id,v.file,t.id LIMIT ?", [*args, 5001]).fetchall()
        run_summaries=_catalog_run_summaries(connection,[observation['id'] for observation in observations])
        phases_by_validation={}
        for validation,phase in run_summaries:phases_by_validation.setdefault(validation,[]).append(phase)
        rows, campaign_ids, build_names = [], set(), {}
        for observation in observations:
            campaign_ids.add(observation["campaign_id"])
            phases=sorted(phases_by_validation.get(observation['id'],[])) or ['observation']
            metadata = json.loads(observation["metadata_json"])
            if observation['build_id'] not in build_names:
                build_metadata=json.loads(observation['build_metadata_json'])
                build_names[observation['build_id']]="PySWF" if observation['engine']=='native' else _dict(build_metadata.get('descriptor')).get('name',observation['engine_name'])
            engine_name=build_names[observation['build_id']]
            for phase in phases:
                phase_summary=run_summaries.get((observation['id'],phase))
                if phase_summary is None:phase_summary=_finish_phase_summary(_new_phase_summary(),observation['status'])
                row = {"campaign_id": observation["campaign_id"], "file": observation["file"], "artifact_sha256": observation["sha256"], "engine": observation["engine"], "engine_name": engine_name, "version": observation["version"], "build_id": observation["build_id"], "operation": observation["operation"], "environment": observation["environment"], "environment_id": observation["environment_id"], "platform": observation["platform"], "language": observation["language"], "phase": phase, **{key:value for key,value in phase_summary.items() if not key.startswith('_')}, "observation_status": observation["status"], "coverage_percent": observation["coverage_percent"], "source_available": observation["source_available"], "expected_units": observation["expected_units"], "compilation": observation["compilation"], "behavior": observation["behavior"], "timing_scope": metadata.get("timing_scope", "includes worker startup" if phase == "cold" else "persistent worker; operation wall time"), "memory_scope": metadata.get("memory_scope", "sampled process-tree RSS"), "comparison": metadata.get("comparison", {}), "warnings": metadata.get("warnings", [])}
                rows.append(row)
                for field in ("valid_performance_summary", "category", "benchmark_role", "process_mode"):
                    row[field] = metadata.get(field)
                for field in ('family_id', 'split', 'adapter_mode', 'inventory_status', 'canonical_inventory_sha256', 'scope', 'include_scope', 'quality_dimensions'):
                    row[field] = metadata.get(field)
                if isinstance(metadata.get('quality_evaluation'), dict):
                    row['quality_evaluation'] = metadata['quality_evaluation']
                row['scope_metrics'] = _dict(metadata.get('quality')).get('scope_metrics')
                row['origin_scope_metrics'] = _dict(metadata.get('quality')).get('origin_scope_metrics')
                row['disassembly_available'] = _dict(metadata.get('quality')).get('disassembly_available')
                row['artifact_unit_scope'] = metadata.get('artifact_unit_scope')
                if phase_summary['_conflicting_execution_flags']:
                    row["warnings"] = [*row["warnings"], "Conflicting execution flags or exit code; execution result is indeterminate"]
                if row["as_error_marker_count"]:
                    row["warnings"] = [*row["warnings"], "ActionScript error markers were reported; exit zero does not prove valid source"]
        campaign_run_counts=dict(connection.execute('SELECT campaign_id,COUNT(*) FROM runs GROUP BY campaign_id'))
        campaign_artifact_counts={row[0]:(row[1],row[2]) for row in connection.execute(
            'SELECT ca.campaign_id,SUM(a.sha256 IS NOT NULL),SUM(a.sha256 IS NULL) '
            'FROM campaign_artifacts ca JOIN artifacts a ON a.id=ca.artifact_id GROUP BY ca.campaign_id')}
        campaigns = []
        for campaign in connection.execute("SELECT c.*,e.label environment,e.platform FROM campaigns c JOIN environments e ON e.id=c.environment_id ORDER BY c.created_at DESC,c.id"):
            if clauses and campaign["id"] not in campaign_ids:
                continue
            identified,unidentified=campaign_artifact_counts.get(campaign['id'],(0,0))
            campaigns.append({key: campaign[key] for key in ("id", "label", "environment", "platform", "operation", "created_at", "source_kind")} | {"run_count":campaign_run_counts.get(campaign['id'],0),"artifact_count":identified,"unidentified_artifact_groups":unidentified})
        tools = [{"id": row["id"], "name": row["name"], "metadata": json.loads(row["metadata_json"])} for row in connection.execute("SELECT * FROM tools ORDER BY id")]
        totals = {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("campaigns", "artifacts", "runs", "tools")}
        totals["artifacts"] = connection.execute("SELECT COUNT(*) FROM artifacts WHERE sha256 IS NOT NULL").fetchone()[0]
        selected_rows = [row for row in rows if all(filters.get(field) is None or row.get(field) == filters[field] or field == 'scope' and filters[field] in (row.get('include_scope') or []) for field in ('scope', 'family_id', 'split', 'adapter_mode'))]
        unit_totals = {}
        for table in ('canonical_units', 'unit_observations'):
            unit_totals[table] = connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] if connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() else 0
        return {"schema_version": 1, "catalog_schema_version": SCHEMA_VERSION, "database": str(path), "totals": totals, "unit_totals": unit_totals, "campaigns": campaigns, "rows": selected_rows[:limit], "tools": tools, "claims": dict(CLAIMS), "truncated": len(selected_rows) > limit or len(observations) > 5000, "warnings": ["Execution success does not prove source equivalence", "Coverage remains null without a verified mapping", "Medians retain campaign, build, operation, environment and phase boundaries", "Peak RSS is sampled and can miss short peaks"]}
    finally:
        connection.close()


def export_catalog_html(database, output):
    """Write a standalone, escaped, filterable evidence page; return its path."""
    page = render_catalog_html(database)
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return str(path)


def render_catalog_html(database):
    """Render the read-only catalog without creating or writing any output file."""
    summary = catalog_summary(database)
    embedded = _json(summary).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return _HTML.replace("__CATALOG_JSON__", embedded)


_HTML = r'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Catálogo de benchmarks Flash</title>
<style>body{font:16px system-ui,sans-serif;background:#f4f7fa;color:#182c39;margin:0}main{max-width:1300px;margin:auto;padding:32px}h1{margin-bottom:8px}.note{background:#fff3cc;padding:16px;border-left:5px solid #b37b16}label{display:inline-flex;flex-direction:column;margin:8px 16px 8px 0;gap:6px}select,input{font:inherit;padding:8px;max-width:260px}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #dce4eb;vertical-align:top}th{background:#e4edf4}.scroll{overflow:auto}small{display:block;color:#526675}.bar{height:14px;background:#287ba1;display:inline-block;vertical-align:middle;margin-right:8px}.metrics{display:flex;gap:24px;flex-wrap:wrap;margin:20px 0}.metrics strong{font-size:26px;display:block}#chart{background:white;padding:16px;margin:18px 0}.chartrow{display:grid;grid-template-columns:minmax(180px,1fr) 2fr;gap:12px;margin:12px 0}button{font:inherit;padding:8px}details{margin:18px 0}noscript{font-weight:bold}</style>
<main><h1>Catálogo de benchmarks Flash</h1><p>Campañas, artefactos y muestras con su procedencia conservada.</p><p class="note">Código de salida cero significa ejecución completada. La equivalencia del código y del comportamiento sigue sin establecerse. Windows y Linux, operaciones, versiones y fases se comparan por separado. RSS representa muestreo.</p><div class="metrics" id="totals"></div><div id="filters"><label>Campaña<select id="campaign"></select></label><label>Entorno<select id="environment"></select></label><label>Herramienta<select id="engine"></select></label><label>Operación<select id="operation"></select></label><label>Fase<select id="phase"></select></label><label>Métrica del gráfico<select id="metric"><option value="time">Tiempo exitoso (ms)</option><option value="rss">RSS observado (MiB)</option><option value="coverage">Cobertura de fuente (%)</option></select></label><label>Buscar archivo<input id="search" type="search"></label></div><p role="status" id="count"></p><details><summary>Procedencia y límites</summary><ul id="provenance"></ul></details><div id="chart" aria-label="Mediciones de una campaña, operación, fase y entorno"></div><div class="scroll"><table><thead><tr><th>Archivo / SHA-256</th><th>Campaña / entorno</th><th>Herramienta / versión</th><th>Operación / fase</th><th>Estado / muestras</th><th>Mediana éxito (ms)</th><th>Hasta error / timeout (ms)</th><th>RSS observado</th><th>Cobertura de fuente</th><th>Compilación / comportamiento</th></tr></thead><tbody id="rows"></tbody></table></div><noscript>Esta página requiere JavaScript para filtrar las muestras incluidas.</noscript></main>
<script type="application/json" id="catalog">__CATALOG_JSON__</script><script>
const data=JSON.parse(document.getElementById('catalog').textContent), campaigns=new Map(data.campaigns.map(c=>[c.id,c]));
const text=(tag,value)=>{const e=document.createElement(tag);e.textContent=value;return e},fmt=v=>v==null?'No medido':Number(v).toLocaleString('es',{maximumFractionDigits:3});
for(const [key,label] of [['campaigns','Campañas'],['artifacts','Artefactos'],['runs','Ejecuciones'],['tools','Herramientas']]){const e=text('div',label);e.prepend(text('strong',data.totals[key]));document.getElementById('totals').append(e)}
for(const key of ['campaign','environment','engine','operation','phase']){const select=document.getElementById(key);select.append(new Option('Todas',''));const values=key==='campaign'?data.campaigns.map(c=>[c.id,c.label+' · '+c.environment]):key==='environment'?[...new Map(data.rows.map(r=>[r.environment_id,r.environment])).entries()]:[...new Set(data.rows.map(r=>r[key]))].sort().map(v=>[v,v]);for(const [value,label] of values)select.append(new Option(label,value));select.addEventListener('change',()=>{if(key==='campaign'&&select.value)setCampaignDefaults(select.value);render()})}
function setCampaignDefaults(id){const cohort=data.rows.filter(r=>r.campaign_id===id);if(!cohort.length)return;document.getElementById('environment').value=cohort[0].environment_id;const operations=[...new Set(cohort.map(r=>r.operation))];const op=operations.includes('source')?'source':operations[0];document.getElementById('operation').value=op;const phases=[...new Set(cohort.filter(r=>r.operation===op).map(r=>r.phase))];document.getElementById('phase').value=['hot','summary','cold','process','observation'].find(p=>phases.includes(p))||phases[0]}
const initial=data.campaigns.find(c=>c.source_kind==='local_report'&&data.rows.some(r=>r.campaign_id===c.id))||data.campaigns[0];if(initial){document.getElementById('campaign').value=initial.id;setCampaignDefaults(initial.id)}document.getElementById('metric').addEventListener('change',render);
for(const c of data.campaigns)document.getElementById('provenance').append(text('li',c.label+' · '+c.source_kind+' · '+c.platform+' · '+c.run_count+' muestras'));
for(const warning of data.warnings)document.getElementById('provenance').append(text('li',warning));document.getElementById('search').addEventListener('input',render);
function render(){const rows=data.rows.filter(r=>['campaign','environment','engine','operation','phase'].every(k=>!document.getElementById(k).value||(k==='campaign'?r.campaign_id:k==='environment'?r.environment_id:r[k])===document.getElementById(k).value)&&r.file.toLowerCase().includes(document.getElementById('search').value.toLowerCase()));document.getElementById('count').textContent=rows.length+' filas encontradas · se muestran hasta 80'+(data.truncated?' · catálogo truncado a 5000 filas':'');const body=document.getElementById('rows');body.replaceChildren();for(const r of rows.slice(0,80)){const tr=document.createElement('tr');const values=[r.file+'\n'+(r.artifact_sha256||'SHA no identificado'),(campaigns.get(r.campaign_id)?.label||r.campaign_id)+'\n'+r.environment,r.engine_name+'\n'+(r.version||'Versión sin registrar'),r.operation+' / '+r.phase,r.status+' · '+r.success_count+'/'+r.run_count+' ejecuciones exitosas; '+r.error_count+' errores; '+r.timeout_count+' timeouts',fmt(r.median_ms),fmt(r.median_error_ms)+' / '+fmt(r.median_timeout_ms),r.peak_tree_rss_bytes==null?'No medido':fmt(r.peak_tree_rss_bytes/1048576)+' MiB',r.coverage_percent==null?'No medido · mapping sin verificar':fmt(r.coverage_percent)+'% ('+r.source_available+'/'+r.expected_units+')',r.compilation+' / '+r.behavior];for(const value of values){const td=text('td',value);td.style.whiteSpace='pre-line';tr.append(td)}body.append(tr)}const chart=document.getElementById('chart'),metric=document.getElementById('metric').value,metricSpec={time:['Tiempo de ejecuciones exitosas','ms',r=>r.median_ms],rss:['RSS observado por muestreo','MiB',r=>r.peak_tree_rss_bytes==null?null:r.peak_tree_rss_bytes/1048576],coverage:['Cobertura de fuente con mapping verificado','%',r=>r.coverage_percent]}[metric];chart.replaceChildren(text('h2',metricSpec[0]));const mixed=['campaign_id','environment_id','operation','phase'].some(key=>new Set(rows.map(r=>r[key])).size>1),builds=new Map();for(const r of rows){if(!builds.has(r.engine))builds.set(r.engine,new Set());builds.get(r.engine).add(r.build_id)}if(mixed||[...builds.values()].some(v=>v.size>1)){chart.append(text('p','La selección mezcla campañas, entornos, operaciones, fases o builds de una herramienta. Selecciona un conjunto único para dibujar el gráfico. Los registros siguen disponibles en la tabla.'));return}chart.append(text('p','Cada barra conserva la identidad del archivo y la herramienta; representa una única campaña, operación, fase y entorno. No se declara ganador.'));if(metric==='coverage')chart.append(text('p','La cobertura describe las unidades declaradas por cada herramienta; los denominadores pueden tener distinto alcance y no prueban equivalencia.'));const measured=rows.filter(r=>metricSpec[2](r)!=null).slice(0,80),max=Math.max(...measured.map(r=>metricSpec[2](r)),1);for(const r of measured){const line=document.createElement('div');line.className='chartrow';line.append(text('span',r.engine_name+' · '+r.file+' · '+r.phase));const value=document.createElement('div'),bar=document.createElement('span');bar.className='bar';bar.style.width=(metricSpec[2](r)/max*80)+'%';bar.setAttribute('aria-hidden','true');value.append(bar,text('span',fmt(metricSpec[2](r))+' '+metricSpec[1]));line.append(value);chart.append(line)}if(!measured.length)chart.append(text('p','No medido: no hay valores observados para esta métrica en la selección.'))}render();
</script></html>'''
