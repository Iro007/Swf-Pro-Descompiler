"""Verified, portable reference toolchain; never modifies system Java or old checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from py_swf import paths


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_download(spec, destination):
    destination = Path(destination)
    if destination.exists() and sha256(destination) == spec["sha256"]:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="pyswf-reference-", suffix=".zip")
    os.close(fd)
    partial = Path(temporary)
    try:
        print("Downloading " + destination.name, flush=True)
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "PySWFStudio-reference-bootstrap"})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        if sha256(partial) != spec["sha256"]:
            raise ValueError("Reference archive checksum mismatch: " + destination.name)
        # Cross-volume temp directories are possible on Windows.
        import shutil
        shutil.copyfile(partial, destination)
        print("SHA256 verified: " + destination.name, flush=True)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def extract_safe(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        for entry in handle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe reference archive member: " + entry.filename)
        handle.extractall(destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="store_true", help="Also clone the pinned source into a NEW directory")
    args = parser.parse_args(argv)
    lock = json.loads((ROOT / "docs/reference-lock.json").read_text(encoding="utf-8"))
    folder = paths.REFERENCES
    downloads = folder / "downloads"
    locations = {}
    for key, spec in (("jdk", lock["temurin"]), ("ffdec", lock["jpexs"]["application"]), ("library", lock["jpexs"]["library"])):
        archive = verified_download(spec, downloads / (key + ".zip"))
        target = folder / key
        marker = target / ".archive-sha256"
        if not marker.exists() or marker.read_text().strip() != spec["sha256"]:
            if target.exists() and any(target.iterdir()):
                raise ValueError(f"Existing reference folder has different content; preserve it: {target}")
            extract_safe(archive, target)
            marker.write_text(spec["sha256"], encoding="ascii")
        locations[key] = target
    java = next(locations["jdk"].glob("*/bin/java.exe"), None)
    javac = next(locations["jdk"].glob("*/bin/javac.exe"), None)
    ffdec = next(locations["ffdec"].rglob("ffdec.jar"), None)
    library = next(locations["library"].rglob("ffdec_lib.jar"), None)
    json_jar = next(locations["ffdec"].rglob("minimal-json*.jar"), None)
    if not all((java, javac, ffdec, library, json_jar)):
        raise ValueError("Reference toolchain archives do not contain the expected files")
    source = paths.VENDOR / "third_party/references/jpexs-26.3.0"
    if args.source:
        if not source.exists():
            subprocess.run(["git", "clone", "--depth", "1", "--branch", lock["jpexs"]["tag"], lock["jpexs"]["repository"], str(source)], check=True)
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if revision != lock["jpexs"]["commit"]:
            raise ValueError("Pinned source differs; refusing to reset an existing checkout")
    config = {"schema_version": 1, "development_only": True, "java": str(java), "javac": str(javac), "ffdec_jar": str(ffdec), "library_jar": str(library), "json_jar": str(json_jar), "source": str(source) if source.exists() else None, "version": lock["jpexs"]["version"], "commit": lock["jpexs"]["commit"], "archives": {key: sha256(downloads / (key + ".zip")) for key in locations}}
    folder.mkdir(exist_ok=True)
    (folder / "toolchain.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    subprocess.run([str(java), "-version"], check=True)
    print(json.dumps(config, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
