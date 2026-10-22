"""Compile the development-only JSONL adapter with the portable JDK."""
import json
import os
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def build():
    config_path = ROOT / ".dev-references/toolchain.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from py_swf.development_paths import normalize_development_config
    config=normalize_development_config(config,ROOT,config_path.parent)
    classes = ROOT / ".dev-references/bridge"
    classes.mkdir(exist_ok=True)
    jars = sorted((ROOT / ".dev-references/library").rglob("*.jar"))
    classpath = os.pathsep.join(map(str, [classes, *jars, Path(config["json_jar"])]))
    sources=[ROOT / 'tools/jpexs_java_bridge/DevelopmentReferenceBridge.java',ROOT / 'tools/jpexs_java_bridge/ParityBridgeInventory.java']
    subprocess.run([config["javac"], "-encoding", "UTF-8", "-cp", classpath, "-d", str(classes), *map(str,sources)], check=True)
    config["reference_classpath"] = classpath
    checked = [*jars, Path(config["json_jar"]), Path(config["ffdec_jar"]).parent / "flashlib/playerglobal32_0.swc", Path(config["ffdec_jar"]).parent / "flashlib/airglobal.swc", *sorted(classes.glob("DevelopmentReferenceBridge*.class")),*sorted(classes.glob('ParityBridgeInventory*.class'))]
    config["artifact_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in checked}
    config["bridge_source_sha256"] = hashlib.sha256((ROOT / "tools/jpexs_java_bridge/DevelopmentReferenceBridge.java").read_bytes()).hexdigest()
    config['bridge_sources_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print("Development reference bridge compiled")


if __name__ == "__main__":
    build()
