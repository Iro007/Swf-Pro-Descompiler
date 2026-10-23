"""Configure pinned Flasm and the existing official Apache Flex swfdump SDK.

Flasm's publisher links an HTTP archive; its HTTPS certificate is expired.
The digest below identifies the official HTTP bytes observed on 2026-10-14,
not an independent publisher checksum or authenticated transport guarantee.
No TLS bypass, global install, commercial license or GUI automation is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bootstrap_references import extract_safe, sha256, verified_download
from py_swf import paths
from py_swf.development_paths import normalize_development_config

FLASM = {
    "url": "http://www.nowrap.de/download/flasm16win.zip",
    "sha256": "0dfbf0447782dd95a2bb1beaeddf9aaec0cb341ee1c3d8e31d54fbc5d680d14d",
}


def setup(*, skip_flasm=False):
    manifest = paths.REFERENCES / "extra-tools.json"
    config = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {
        "schema_version": 1, "development_only": True, "tools": {}}
    if config.get("development_only") is not True:
        raise ValueError("External benchmark configuration must be development-only")
    before = json.loads(json.dumps(config.get("tools", {})))
    sdk = paths.TOOLPACKS / "flex"
    lock = json.loads((paths.TOOLPACKS / "lock.json").read_text(encoding="utf-8"))["tools"]["flex"]
    reference = normalize_development_config(json.loads((paths.REFERENCES / "toolchain.json").read_text(encoding="utf-8")), paths.WORKSPACE)
    java = Path(reference["java"])
    jar = sdk / "lib/swfdump.jar"
    with zipfile.ZipFile(jar) as handle:
        metadata = handle.read("META-INF/MANIFEST.MF").decode("utf-8")
    if "Implementation-Version: 4.16.1.20171115" not in metadata:
        raise ValueError("Existing Flex swfdump does not match SDK 4.16.1")
    # Include the complete declared JAR classpath and retained distribution notices.
    artifacts = [java, jar, sdk / "lib/asc.jar", sdk / "lib/swfutils.jar", sdk / "LICENSE", sdk / "NOTICE"]
    config["tools"]["flex_swfdump"] = {
        "version": "4.16.1.20171115", "executable": str(java.resolve()),
        "library_jar": str(jar.resolve()), "adapter_mode": "swfx_assembly_file",
        "artifact_sha256": {str(path.resolve()): sha256(path) for path in artifacts},
        "license": "Apache-2.0; SDK LICENSE/NOTICE retain dependency terms",
        "archive": {"url": lock["source"], "sha256": lock["archive_sha256"]},
        "hash_authority": "SDK archive digest retained from existing local toolpack lock; executable, JAR classpath and notices hashed locally",
        "source": "https://github.com/apache/flex-sdk/blob/apache-flex-sdk-4.16.1/modules/swfutils/src/java/flash/swf/tools/SwfxPrinter.java",
        "capabilities": {"disassemble": ["as2", "as3"]},
        "artifact_unit_scope": "file", "unit_mapping": "not_established", "development_only": True,
    }
    if not skip_flasm:
        archive = verified_download(FLASM, paths.REFERENCES / "downloads/flasm-1.62-win.zip")
        destination = paths.REFERENCES / "extra-tools/flasm-1.62"
        marker = destination / ".archive-sha256"
        if not marker.is_file():
            if destination.is_dir() and any(destination.iterdir()):
                raise ValueError("Preserve existing Flasm directory; refusing to overwrite it")
            extract_safe(archive, destination)
            marker.write_text(FLASM["sha256"], encoding="ascii")
        elif marker.read_text(encoding="ascii").strip() != FLASM["sha256"]:
            raise ValueError("Existing Flasm directory belongs to another archive")
        license_text = (destination / "LICENSE.TXT").read_text(encoding="ascii")
        if "Redistribution and use in source and binary forms" not in license_text:
            raise ValueError("Flasm distribution license was not found")
        config["tools"]["flasm"] = {
            "version": "1.62", "executable": str((destination / "flasm.exe").resolve()),
            "adapter_mode": "avm1_assembly_file", "license": "BSD-3-Clause; retain LICENSE.TXT",
            "artifact_sha256": {str(path.resolve()): sha256(path) for path in destination.rglob("*") if path.is_file()},
            "archive": FLASM, "publisher_page": "http://www.nowrap.de/flasm.html",
            "hash_authority": "Locally measured official upstream HTTP archive on 2026-10-14; no publisher digest; transport not authenticated",
            "capabilities": {"disassemble": ["as2"]}, "artifact_unit_scope": "file",
            "unit_mapping": "not_established", "development_only": True,
        }
    # Existing engine records are preserved exactly, including historical paths.
    for engine, record in before.items():
        if engine not in ("flasm", "flex_swfdump") and config["tools"][engine] != record:
            raise ValueError("Unrelated engine configuration changed")
    manifest.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"configured": [key for key in ("flex_swfdump", "flasm") if key in config["tools"]],
            "manifest": str(manifest), "flasm_archive": FLASM if not skip_flasm else None,
            "performance_claim": "none; setup does not measure tools"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-flasm", action="store_true")
    args = parser.parse_args()
    print(json.dumps(setup(skip_flasm=args.skip_flasm), indent=2))
