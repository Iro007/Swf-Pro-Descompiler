"""Install isolated optional compiler packs; never alter system PATH/haxelib.

Pinned upstream archives and actual file hashes are recorded in vendor/toolpacks/lock.
Run with the project .venv. JPEXS application bridge is built independently
from the development benchmark bridge.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf import paths
ROOT = paths.WORKSPACE
PACKS = paths.TOOLPACKS
from py_swf.suite_tools import ToolRegistry, fingerprint, run_tool


def download(url, filename):
    target = PACKS / "downloads" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        request = urllib.request.Request(url, headers={"User-Agent": "FlashUniversalStudio/0.2"})
        with urllib.request.urlopen(request, timeout=90) as response, target.with_suffix(".partial").open("wb") as output:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 512 * 1024**2:
                    raise ValueError("Tool archive exceeds download budget")
                output.write(block)
        os.replace(target.with_suffix(".partial"), target)
    return target


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        if sum(i.file_size for i in zipped.infolist()) > 1024 * 1024**2:
            raise ValueError("Tool archive exceeds expansion budget")
        for info in zipped.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        zipped.extractall(destination)


def build_bridge():
    application_source = PROJECT / "tools/jpexs_application_bridge/UniversalBridge.java"
    destination = PACKS / "jpexs-bridge"
    destination.mkdir(parents=True, exist_ok=True)
    # Production owns its source as well as its class/build/lock. Do not
    # regenerate it from the independently pinned development benchmark bridge.
    text = application_source.read_text(encoding="utf-8")
    source = destination / "UniversalBridge.java"
    source.write_text(text, encoding="utf-8")
    registry = ToolRegistry()
    jar = Path(registry.executable("jpexs"))
    java = Path(registry.executable("java"))
    javac = java.with_name("javac.exe" if os.name == "nt" else "javac")
    classpath = os.pathsep.join(map(str, [jar, jar.parent / "lib/*"]))
    helper=PROJECT/'tools/jpexs_java_bridge/ParityBridgeInventory.java'
    log = run_tool([str(javac), "-encoding", "UTF-8", "-cp", classpath, "-d", str(destination), str(source),str(helper)], timeout=90)
    return {"source_sha256": fingerprint(source), "shared_helper_sha256":fingerprint(helper), "jar_sha256": fingerprint(jar), "classes": {p.name: fingerprint(p) for p in [*destination.glob("UniversalBridge*.class"),*destination.glob('ParityBridgeInventory*.class')]},
            "libraries": {p.name: fingerprint(p) for p in (jar.parent / "lib").glob("*.jar")}, "log": log}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--haxe", action="store_true")
    parser.add_argument("--flixel", action="store_true")
    parser.add_argument("--flex", action="store_true")
    parser.add_argument("--jpexs-bridge", action="store_true")
    args = parser.parse_args()
    PACKS.mkdir(exist_ok=True)
    lock_path = PACKS / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {"schema_version": 1, "tools": {}, "archive_hash_authority": "locally_measured; upstream checksum only when supplied"}
    if args.haxe or args.flixel:
        for key, version, url, filename in (
            ("haxe", "4.3.7", "https://github.com/HaxeFoundation/haxe/releases/download/4.3.7/haxe-4.3.7-win64.zip", "haxe-4.3.7-win64.zip"),
            ("neko", "2.4.1", "https://github.com/HaxeFoundation/neko/releases/download/v2-4-1/neko-2.4.1-win64.zip", "neko-2.4.1-win64.zip"),
        ):
            archive = download(url, filename)
            extract(archive, PACKS / key)
            lock["tools"][key] = {"version": version, "source": url, "archive_sha256": fingerprint(archive)}
            print(f"Installed isolated {key} {version}", flush=True)
    if args.flex:
        url = "https://archive.apache.org/dist/flex/4.16.1/binaries/apache-flex-sdk-4.16.1-bin.zip"
        archive = download(url, "apache-flex-sdk-4.16.1-bin.zip")
        extract(archive, PACKS / "flex")
        player = paths.REFERENCES / "ffdec/flashlib/playerglobal32_0.swc"
        if player.is_file():
            target = PACKS / "flex/frameworks/libs/player/32.0/playerglobal.swc"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(player, target)
        lock["tools"]["flex"] = {"version": "4.16.1", "source": url, "archive_sha256": fingerprint(archive), "playerglobal_source": "user local FFDec SDK; not included in distribution"}
        print("Installed isolated Flex 4.16.1", flush=True)
    if args.jpexs_bridge:
        lock["tools"]["jpexs-bridge"] = build_bridge()
        print("Built independent application JPEXS bridge", flush=True)
    if args.flixel:
        registry = ToolRegistry()
        env = registry.environment()
        haxelib = registry.executable("haxeflixel")
        local_repo = PACKS / "haxelib"
        local_repo.mkdir(exist_ok=True)
        actual = run_tool([haxelib, "config"], env=env, cwd=PACKS, timeout=10).strip()
        if Path(actual).resolve() != local_repo.resolve():
            raise ValueError("Haxelib did not honor isolated HAXELIB_PATH; refusing global installation")
        for name, version in (("flixel", "6.2.0"), ("openfl", "9.5.2"), ("lime", "8.3.2")):
            output = run_tool([haxelib, "install", name, version, "--always"], env=env, cwd=PACKS, timeout=600)
            print(f"Installed {name} {version}: {output[-1500:]}", flush=True)
            package = local_repo / name / version.replace(".", ",")
            lock["tools"][name] = {"version": version, "published_source": f"https://lib.haxe.org/p/{name}/{version}/", "haxelib_json_sha256": fingerprint(package / "haxelib.json") if (package / "haxelib.json").is_file() else None}
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    print(json.dumps(ToolRegistry().catalog(), ensure_ascii=False))


if __name__ == "__main__":
    main()
