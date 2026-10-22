"""Install and register swfmill and swfextract as hash-pinned benchmark tools.

swfmill: official Windows build from swfmill.org (HTTPS, digest measured here).
swfextract: the SWFTools project no longer publishes Windows binaries, so it
is built from the official GitHub source at a pinned commit with the MinGW
toolchain that rust_swf/bootstrap.ps1 installs. The source config.h needs two
local adjustments for modern GCC/Windows headers (see BUILD_NOTES).
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DOWNLOADS = ROOT / ".dev-references/downloads"
TOOLPACKS = WORKSPACE / "toolpacks"
MINGW = WORKSPACE / "rust_swf/tools/bootstrap/mingw/mingw64/bin"

SWFMILL = {"url": "https://www.swfmill.org/releases/swfmill-0.3.6-win32.zip", "version": "0.3.6",
           "sha256": "6a0e3630518088db4645bf246f79ed7f8a2ed8e981bc41ac3fc43d7599b24f4b"}
SWFTOOLS = {"commit": "56a672207ac699e63908557b8888087f33e72fd4", "version": "0.9.2",
            "url": "https://codeload.github.com/swftools/swftools/tar.gz/56a672207ac699e63908557b8888087f33e72fd4",
            "sha256": "66f48cf522af5d251f940b4203d855bec6e6ebbe043aed4d3d87bb7cbe0919d5"}
BUILD_NOTES = ("configure with CC=gcc; config.h: 'typedef unsigned char boolean;' instead of '#define boolean int' "
               "(Windows rpcndr.h); Makefile.common: -std=gnu11 and -Wno-error for implicit declarations, "
               "pointer/int conversions and return mismatches (2012 C under GCC 16)")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        subprocess.run(["curl", "-sS", "-L", "--fail", "--max-time", "300", "-o", str(target), url], check=True)
    if sha256(target) != expected:
        raise ValueError(f"SHA-256 mismatch for {target}")
    return target


def install_swfmill():
    archive = download(SWFMILL["url"], DOWNLOADS / "swfmill-0.3.6-win32.zip", SWFMILL["sha256"])
    folder = TOOLPACKS / "swfmill-0.3.6"
    with zipfile.ZipFile(archive) as handle:
        for item in handle.infolist():
            if item.is_dir() or "/" in item.filename or "\\" in item.filename:
                raise ValueError("Unexpected path in swfmill archive: " + item.filename)
        handle.extractall(folder)
    return folder / "swfmill.exe", [folder / "COPYING.txt"]


def build_swfextract():
    archive = download(SWFTOOLS["url"], DOWNLOADS / f"swftools-{SWFTOOLS['commit']}.tar.gz", SWFTOOLS["sha256"])
    source = ROOT / ".dev-references/extra-tools/swftools-src"
    if not (source / "src/swfextract.exe").is_file():
        source.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as handle:
            handle.extractall(source, filter=lambda member, path: member.replace(name=member.name.split("/", 1)[-1]) if "/" in member.name else None)
        env = {**os.environ, "PATH": str(MINGW) + os.pathsep + os.environ["PATH"], "CC": "gcc", "CXX": "g++"}
        subprocess.run(["bash", "./configure", "--disable-lame"], cwd=source, env=env, check=True)
        config = source / "config.h"
        config.write_text(config.read_text().replace("#define boolean int", "typedef unsigned char boolean;"))
        common = source / "Makefile.common"
        flags = ("-std=gnu11 -Wno-error=implicit-function-declaration -Wno-error=incompatible-pointer-types "
                 "-Wno-error=int-conversion -Wno-error=implicit-int -Wno-error=return-mismatch ")
        common.write_text(common.read_text().replace("C=gcc -c $(DEFS)", "C=gcc -c " + flags + "$(DEFS)", 1))
        subprocess.run(["mingw32-make", "libbase.lib", "librfxswf.lib"], cwd=source / "lib", env=env, check=True)
        subprocess.run(["mingw32-make", "swfextract.exe"], cwd=source / "src", env=env, check=True)
    folder = TOOLPACKS / "swftools"
    folder.mkdir(parents=True, exist_ok=True)
    for name, origin in (("swfextract.exe", source / "src/swfextract.exe"), ("COPYING", source / "COPYING")):
        (folder / name).write_bytes(origin.read_bytes())
    return folder / "swfextract.exe", [folder / "COPYING"]


def setup():
    manifest_path = ROOT / ".dev-references/extra-tools.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("development_only") is not True:
        raise ValueError("Existing manifest must be marked development_only")
    swfmill, swfmill_notices = install_swfmill()
    manifest["tools"]["swfmill"] = {
        "version": SWFMILL["version"], "license": "GPL-2.0-or-later", "executable": str(swfmill.resolve()),
        "artifact_sha256": {str(p.resolve()): sha256(p) for p in [swfmill, *swfmill_notices]},
        "archive": {"url": SWFMILL["url"], "sha256": SWFMILL["sha256"]},
        "hash_authority": "Official swfmill.org Windows build over HTTPS; archive digest measured at registration",
        "capabilities": {"xml": ["as2", "as3"]}, "development_only": True}
    swfextract, swftools_notices = build_swfextract()
    manifest["tools"]["swfextract"] = {
        "version": SWFTOOLS["version"], "license": "GPL-2.0-or-later", "executable": str(swfextract.resolve()),
        "artifact_sha256": {str(p.resolve()): sha256(p) for p in [swfextract, *swftools_notices]},
        "archive": {"url": SWFTOOLS["url"], "sha256": SWFTOOLS["sha256"], "commit": SWFTOOLS["commit"]},
        "hash_authority": "Built locally from the official swftools/swftools source at a pinned commit; " + BUILD_NOTES,
        "capabilities": {"extract": ["as2", "as3"]}, "development_only": True}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"configured": ["swfmill", "swfextract"], "config": str(manifest_path)}


if __name__ == "__main__":
    print(json.dumps(setup(), indent=2))
    sys.exit(0)
