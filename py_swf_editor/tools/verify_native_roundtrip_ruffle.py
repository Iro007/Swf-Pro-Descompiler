"""One bounded native source round-trip checked by independent Ruffle AVM2."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.compiler.class_compiler import compile_class_program,class_program_swf
from py_swf.engines import NativePythonEngine
from py_swf.script_inventory import enumerate_scripts
from tools.verify_ruffle import verify

SOURCE='''package roundtrip { import flash.display.Sprite;
public class Main extends Sprite {
 public function Main(){super();this.graphics.beginFill(255);this.graphics.drawRect(40,40,160,100);this.graphics.endFill();trace("ROUNDTRIP_VALUE");trace(calculate(4));trace("ROUNDTRIP_OK");}
 public function calculate(n:int):int {var value:int=n*3;return value+2;}
} }'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'reports/native-ruffle-roundtrip');args=parser.parse_args()
    folder=args.output;folder.mkdir(parents=True,exist_ok=True)
    (folder/'Main.as').write_text(SOURCE,encoding='utf-8')
    original=class_program_swf(compile_class_program(SOURCE),document_class='roundtrip.Main',width=640,height=480)
    unit=next(unit for unit in enumerate_scripts(original) if unit.name.endswith('.calculate'))
    result=NativePythonEngine().decompile(original,unit.tag_path[0])
    section=next(section for section in result['sections'] if section.get('body_index')==unit.body_index)
    if section.get('error') or not section.get('editable_source'):
        raise ValueError('The fixture method did not reconstruct safely: '+str(section))
    (folder/'decompiled-method.as').write_text(section['source'],encoding='utf-8')
    recompiled=NativePythonEngine().edit_unit(original,unit.metadata(),section['source'],source=True)
    a,b=folder/'original.swf',folder/'recompiled.swf';a.write_bytes(original);b.write_bytes(recompiled)
    runtime=verify([('roundtrip-original',a,'ROUNDTRIP_OK'),('roundtrip-recompiled',b,'ROUNDTRIP_OK')],ROOT.parent/'rust_swf/web/public/ruffle',folder,30,load_mode='data')
    expected=['ROUNDTRIP_VALUE','14','ROUNDTRIP_OK']
    passed=runtime['passed'] and all(row['traces']==expected for row in runtime['fixtures'])
    report={'passed':passed,'engine':'native','unit':unit.metadata(),'reconstructed_source':section['source'],'original_sha256':hashlib.sha256(original).hexdigest(),'recompiled_sha256':hashlib.sha256(recompiled).hexdigest(),'expected_traces':expected,'runtime':'Ruffle 0.7.1 / Chromium','behavior_verified':passed,'scope':'One authored integer-arithmetic method; whole-document initialization and rendering exercised, no global decompiler parity claim','runtime_report':str((folder/'runtime-report.json').resolve())}
    (folder/'roundtrip-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('NATIVE SOURCE ROUNDTRIP', 'PASS' if passed else 'FAIL',section['source'],flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
