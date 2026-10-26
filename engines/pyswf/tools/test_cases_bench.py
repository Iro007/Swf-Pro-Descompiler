from pathlib import Path
from py_swf.universal import UniversalSuite
from run_semantic_benchmarks import native_documents, assert_complete_class_set

cases = [
    "corpus/parity-20261014/swf/dev-as2-timeline/release.fws.swf",
    "corpus/parity-20261014/swf/dev-as2-captures/release.fws.swf",
    "corpus/parity-20261014/swf/dev-as3-classes-namespaces/release.fws.swf",
    "corpus/parity-20261014/swf/dev-as3-closures/release.fws.swf",
    "corpus/parity-20261014/swf/dev-as3-control-handlers/release.fws.swf",
    "corpus/parity-20261014/swf/dev-haxe-application/release.fws.swf",
]

suite = UniversalSuite()
for c in cases:
    path = Path(c)
    if not path.exists():
        continue
    data = path.read_bytes()
    suite.open(path)
    print(f"\n=== Case: {path.parent.name} ===")
    try:
        docs = native_documents(suite, "native")
        print(f"  native_documents count: {len(docs)}")
        try:
            assert_complete_class_set(docs, data)
            print("  assert_complete_class_set: PASSED!")
        except Exception as e:
            print("  assert_complete_class_set FAILED:", e)
    except Exception as e:
        print("  native_documents FAILED:", e)

suite.close()
