"""Small, reproducible compiler campaign using only the bytecode CLI VM."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from py_swf.avm1 import assemble_avm1
from py_swf.compiler import compile_actionscript
from tests.native_control_vm import run_bytes
from tests.test_native_closure_compiler import as3

CASES=[
 ('as2-for-in','as2','var sum=0;for(var key in data){sum+=data[key];}return sum;',[],31,{'data':{'a':12,'b':19}}),
 ('as3-for-each','as3','var sum:int=0;for each(var value:int in [4,5,6]){sum+=value;}return sum;',[],15,None),
 ('as2-live-closures','as2','function make(n){return function(){n++;return n;};}var a=make(1);var b=make(10);trace(a());trace(b());trace(a());',[2,11,3],None,None),
 ('as3-live-closures','as3','function make(start:int):Function{var n:int=start;return function():int{n++;return n;};}var a=make(1);var b=make(10);trace(a());trace(b());trace(a());',[2,11,3],None,None),
 ('as3-grandchild','as3','function make(a:int):Function{var n:int=a;return function(b:int):Function{var k:int=b;return function():int{n++;k++;return n+k;};};}var mid=make(1);var f=mid(10);var g=mid(20);trace(f());trace(g());trace(f());',[13,24,16],None,None),
 ('as3-exception-scope','as3','var n:int=1;var f=function():int{return n;};try{throw "x";}catch(e){n=2;trace(f());}finally{n=3;trace(f());}trace(f());',[2,3,3],None,None),
]


def main():
    folder=ROOT/'reports/native-compiler-cli';folder.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'engine':'PySWF','verification':'independent bounded bytecode VM','gui_started':False,'browser_started':False,'flash_runtime_verified':False,'cases':[]}
    for name,target,source,traces,value,variables in CASES:
        (folder/(name+'.as')).write_text(source,encoding='utf-8')
        if target=='as2':
            binary=assemble_avm1(compile_actionscript(source,target));result=run_bytes(binary,target,variables=variables);extension='avm1'
        else:
            result,abc,assembly=as3(source,variables);binary=abc.serialize();extension='abc'
            (folder/(name+'.pcode')).write_text(str(assembly),encoding='utf-8')
        (folder/(name+'.'+extension)).write_bytes(binary)
        passed=not result.raised and result.traces==traces and result.value==value
        report['cases'].append({'case':name,'language':target,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'bytecode_sha256':hashlib.sha256(binary).hexdigest(),'traces':result.traces,'value':result.value,'passed':passed})
        print(name,'PASS' if passed else 'FAIL',result.traces,result.value,flush=True)
    report['passed']=all(case['passed'] for case in report['cases'])
    report['limitations']=['Owned object/array/scalar fixtures only; no global language or Flash parity claim','Opaque activation editing guards retained','No GUI, browser, Ruffle or external runtime was started']
    (folder/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
