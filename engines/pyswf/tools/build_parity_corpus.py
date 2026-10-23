"""Author and compile twelve independent project families; never fake compilers.

Use --prepare-only while bootstrapping optional compilers. A completed manifest
is sealed; reruns verify it instead of replacing its sources or binaries.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import sys
import zlib

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf import paths
from py_swf.canonical_inventory import build_inventory
from py_swf.swf_parser import decompress_body
from py_swf.suite_tools import ToolRegistry, run_tool, fingerprint


def as3(body, helpers=''):
    return 'package { import flash.display.Sprite; public class Main extends Sprite { public function Main(){ super(); ' + body + ' } ' + helpers + ' } }'


def haxe(body, helpers=''):
    return 'class Main { static function main(){ ' + body + ' } ' + helpers + ' }'


def flixel(label, advanced=False):
    return {
        'Project.xml': '<project><meta title="Parity '+label+'" package="parity.'+label+'" version="1.0.0" company="Local authored corpus"/><app main="Main" file="ParityGame" path="export"/><window width="320" height="240" fps="30"/><source path="source"/><haxelib name="flixel" version="6.2.0"/><haxedef name="FLX_NO_GAMEPAD"/><haxedef name="FLX_NO_MOUSE_ADVANCED"/></project>',
        'source/Main.hx': 'import flixel.FlxGame; import flash.display.Sprite; class Main extends Sprite { public function new(){super();addChild(new FlxGame(320,240,PlayState));} }',
        'source/PlayState.hx': 'import flixel.FlxState; import flixel.FlxSprite; class PlayState extends FlxState { override public function create(){super.create();var tile=new FlxSprite(20,20).makeGraphic(40,40,0xff33aa11);add(tile);'
             + ('var points=[2,3,5];var sum=0;for(p in points){sum+=p*p;}trace("PARITY_'+label.upper()+'_"+sum);' if advanced else 'trace("PARITY_'+label.upper()+'_READY");')
             + '} }'}


def project_specs():
    """Authored application sources, CC0-1.0; independent family split is fixed."""
    return [
      {'family_id':'dev-as2-timeline','split':'development','compiler':'rascal','language':'as2','entry':'Main.as',
       'sources':{'Main.as':'var score = 0; var values = [2, 3, 5]; for (var i = 0; i < values.length; i++) { score += values[i]; } trace("PARITY_TIMELINE_" + score);'},'expected_trace':['PARITY_TIMELINE_10']},
      {'family_id':'dev-as2-captures','split':'development','compiler':'rascal','language':'as2','entry':'Main.as',
       'sources':{'Main.as':'function factory(base) { return function(step) { return base + step; }; } var add = factory(7); trace("PARITY_CAPTURE_" + add(5));'},'expected_trace':['PARITY_CAPTURE_12']},
      {'family_id':'dev-as3-classes-namespaces','split':'development','compiler':'flex','language':'as3','entry':'Main.as',
       'sources':{'Main.as':as3('var model:Model = new Model(4); trace("PARITY_NAMESPACE_"+model.add(3));').replace('import flash.display.Sprite;', 'import flash.display.Sprite; import app.Model;'),
                  'app/localns.as':'package app { public namespace localns="urn:parity:model"; }',
                  'app/Model.as':'package app { public class Model { private var total:int; public function Model(value:int){ total=value; } public function add(n:int):int { total+=n;return total; } localns function label():String{return "model";} public function get value():int{return total;} public function set value(n:int):void{total=n;} } }'},
       'application_classes':['Main','app::Model'],'expected_trace':['PARITY_NAMESPACE_7']},
      {'family_id':'dev-as3-closures','split':'development','compiler':'flex','language':'as3','entry':'Main.as',
       'sources':{'Main.as':as3('var f:Function=make(6);trace("PARITY_CLOSURE_"+f(4));','private function make(base:int):Function { return function(step:int):int{return base+step;}; }')},
       'application_classes':['Main'],'expected_trace':['PARITY_CLOSURE_10']},
      {'family_id':'dev-as3-control-handlers','split':'development','compiler':'flex','language':'as3','entry':'Main.as',
       'sources':{'Main.as':as3('trace("PARITY_HANDLER_"+calculate(3));','private function calculate(n:int):int { var sum:int=0; try { for(var i:int=0;i<n;i++){switch(i){case 0:sum+=2;break;case 1:sum+=3;break;default:sum+=5;}} if(n<0) throw new Error("negative"); } catch(error:Error) {sum=-1;} finally {sum+=1;} return sum; }')},
       'application_classes':['Main'],'expected_trace':['PARITY_HANDLER_11']},
      {'family_id':'dev-haxe-application','split':'development','compiler':'haxe','language':'as3','entry':'Main.hx',
       'sources':{'Main.hx':haxe('var items=[1,2,3];var sum=0;for(item in items)sum+=item*2;flash.Lib.trace("PARITY_HAXE_"+sum);')},
       'application_classes':['Main'],'expected_trace':['PARITY_HAXE_12']},
      {'family_id':'validation-as2-prototype-app','split':'validation','compiler':'rascal','language':'as2','entry':'Main.as',
       'sources':{'Main.as':'function Counter(start) { this.value = start; } Counter.prototype.add = function(amount) { this.value += amount; return this.value; }; var counter = new Counter(10); trace("PARITY_PROTOTYPE_" + counter.add(4));'},'expected_trace':['PARITY_PROTOTYPE_14']},
      {'family_id':'validation-flex-app','split':'validation','compiler':'flex','language':'as3','entry':'Main.as',
       'sources':{'Main.as':as3('var board:Board=new Board();trace("PARITY_FLEXAPP_"+board.total());graphics.beginFill(0x33aa11);graphics.drawRect(20,20,40,40);graphics.endFill();').replace('import flash.display.Sprite;', 'import flash.display.Sprite; import app.Board;'),
                  'app/Board.as':'package app { public class Board { private var rows:Array=[3,4,8]; public function Board(){} public function total():int{var sum:int=0;for each(var row:int in rows){sum+=row;}return sum;} } }'},
       'application_classes':['Main','app::Board'],'expected_trace':['PARITY_FLEXAPP_15']},
      {'family_id':'validation-haxeflixel-game','split':'validation','compiler':'haxeflixel','language':'as3','entry':'Project.xml','sources':flixel('validation'),
       'application_classes':['Main','PlayState'],'expected_trace':['PARITY_VALIDATION_READY']},
      {'family_id':'holdout-as2-independent','split':'holdout','compiler':'rascal','language':'as2','entry':'Main.as',
       'sources':{'Main.as':'var records = [{name:"red",value:3},{name:"blue",value:7}]; var output = ""; for (var i = 0; i < records.length; i++) { if(records[i].value > 4) { output += records[i].name; } } trace("PARITY_HOLDOUT_AS2_" + output);'},'expected_trace':['PARITY_HOLDOUT_AS2_blue']},
      {'family_id':'holdout-as3-ledger-events','split':'holdout','compiler':'flex','language':'as3','entry':'Main.as',
       'evaluation_state':'decompiler observations withheld until implementation freeze',
       'sources':{'Main.as':as3('ledger={cash:12,credit:7,refund:3};addEventListener("ready",onReady);dispatchEvent(new Event("ready"));graphics.beginFill(0x33aa11);graphics.drawRect(20,20,40,40);graphics.endFill();',
                   'private var ledger:Object; private function onReady(event:Event):void{var total:int=int(ledger.cash)+int(ledger.credit)-int(ledger.refund);trace("PARITY_HOLDOUT_LEDGER_"+total);}')
                   .replace('import flash.display.Sprite;', 'import flash.display.Sprite; import flash.events.Event;')},
       'application_classes':['Main'],'expected_trace':['PARITY_HOLDOUT_LEDGER_16']},
      {'family_id':'holdout-haxeflixel-independent','split':'holdout','compiler':'haxeflixel','language':'as3','entry':'Project.xml','sources':flixel('holdout',True),
       'application_classes':['Main','PlayState'],'expected_trace':['PARITY_HOLDOUT_38']},
    ]


def compression_variant(data, signature):
    """Change only the compression wrapper; retain exact tag/body offsets."""
    body=decompress_body(data[:3].decode(),data[8:],int.from_bytes(data[4:8],'little'))
    version=max(data[3],13) if signature=='ZWS' else data[3]
    if signature=='FWS': encoded=body
    elif signature=='CWS': encoded=zlib.compress(body)
    elif signature=='ZWS':
        raw=lzma.compress(body,format=lzma.FORMAT_ALONE); payload=raw[13:]
        encoded=len(payload).to_bytes(4,'little')+raw[:5]+payload
    else: raise ValueError('Unknown SWF signature')
    return signature.encode()+bytes([version])+(len(body)+8).to_bytes(4,'little')+encoded


def write_new(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data: raise ValueError('Refusing to replace corpus file: '+str(path))
    else: path.write_bytes(data)


def tree_hash(path):
    rows=[{'relative':p.relative_to(path).as_posix(),'sha256':fingerprint(p),'bytes':p.stat().st_size}
          for p in sorted(path.rglob('*')) if p.is_file()]
    return {'tree_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'files':len(rows),'bytes':sum(r['bytes'] for r in rows)}


def compile_family(spec, folder, debug, registry, rascal=None):
    entry=folder/spec['entry']; result=folder/'builds'/('debug' if debug else 'release')/'compiler.swf'
    if result.exists():
        provenance=result.parent/'compiler-provenance.json'
        if provenance.is_file():
            record=json.loads(provenance.read_text(encoding='utf-8'))
            if record['compiler_output_sha256']!=fingerprint(result):raise ValueError('Existing compiler output changed')
            return result,record
        # A rejected/unvalidated compiler output stays available as evidence.
        rejected=result.with_name('compiler-unvalidated-'+fingerprint(result)[:12]+'.swf')
        if rejected.exists():raise ValueError('Existing unvalidated output is ambiguous; preserve and inspect')
        result.rename(rejected)
    result.parent.mkdir(parents=True,exist_ok=True)
    compiler=spec['compiler']; env=registry.environment()
    if compiler=='flex':
        executable=Path(registry.executable('flex')); frameworks=executable.parent.parent/'frameworks'
        command=[registry.executable('java'),'-Xmx1024m','-jar',str(executable),'+flexlib='+str(frameworks),
                 '+playerglobalHome='+str(frameworks/'libs/player'),'-target-player=32.0','-debug='+str(debug).lower(),
                 '-omit-trace-statements=false','-output='+str(result),'-source-path='+str(folder),str(entry)]
        env['PLAYERGLOBAL_HOME']=str(frameworks/'libs/player')
    elif compiler=='haxe':
        command=[registry.executable('haxe'),'-cp',str(folder),'-main','Main','--swf',str(result),'--swf-version','11.8']
        if debug: command+=['--debug']
    elif compiler=='haxeflixel':
        command=[registry.executable('haxeflixel'),'run','lime','build','flash','-debug' if debug else '-release']
    elif compiler=='rascal':
        if not rascal: raise ValueError('Independent AS2 compiler unavailable; PySWF fixture substitution is forbidden')
        # Root supplies the verified command template: explicit placeholders,
        # never a shell expression or an inferred fallback compiler.
        command=[part.replace('{source}',str(entry)).replace('{output}',str(result)).replace('{debug}',str(debug).lower())
                     .replace('{profile}','debug' if debug else 'release') for part in rascal]
    else: raise ValueError('Unknown compiler')
    log=run_tool(command,cwd=folder,env=env,timeout=180)
    if compiler=='haxeflixel':
        candidates=list((folder/'export').rglob('ParityGame.swf'))
        # Lime uses the same flash/bin path for debug/release; the exact
        # requested flags and copied output hash establish build provenance.
        if len(candidates)!=1: raise ValueError('HaxeFlixel did not produce one requested build: '+str(candidates))
        write_new(result,candidates[0].read_bytes())
    if not result.is_file(): raise ValueError('Compiler produced no SWF')
    inventory=build_inventory(result.read_bytes())
    if inventory['diagnostics']: raise ValueError('Compiler output inventory invalid: '+str(inventory['diagnostics']))
    record={'status':'compiled','compiler':compiler,'debug':debug,'command':command,'log':log,
            'binary_sha256':fingerprint(command[0]),'compiler_output_sha256':fingerprint(result),
            'behavior':'not_run','source_equivalence':'not_verified'}
    if compiler=='rascal' and not any('{profile}' in part or '{debug}' in part for part in (rascal or [])):
        record['debug_mode']='unavailable_in_rascal_0.3.5_cli; debug/release corpus labels compile identical options'
    elif compiler=='rascal':
        record['debug_mode']='explicit independent compiler profile: ' + ('debug' if debug else 'release')
    (result.parent/'compiler-provenance.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return result,record


def build(destination, *, prepare_only=False, rascal=None):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    manifest_path=destination/'manifest.json'
    if manifest_path.exists():
        from py_swf.corpus import read_immutable_manifest
        return read_immutable_manifest(destination)
    specs=project_specs()
    for spec in specs:
        folder=destination/'projects'/spec['family_id']
        for filename,content in spec['sources'].items():
            source=folder/filename;source.parent.mkdir(parents=True,exist_ok=True)
            # Sources are editable only before the manifest has been sealed.
            source.write_bytes(content.encode('utf-8'))
        write_new(folder/'LICENSE.txt',b'CC0-1.0. Authored locally for this benchmark; no third party game source.\n')
    if prepare_only:
        return {'status':'sources_prepared','families':len(specs),'split_families':{'development':6,'validation':3,'holdout':3}}
    registry=ToolRegistry(destination/'tool-registry.json')
    previous_state=destination/'build-state.json'
    # Toolpacks are not mutated by this builder. Retain the completed hash
    # snapshot while repairing authored sources/optional compiler availability.
    previous=json.loads(previous_state.read_text(encoding='utf-8')) if previous_state.is_file() else {}
    dependencies=previous.get('dependencies',{})
    for name in ('haxe','haxelib','flex'):
        path=paths.TOOLPACKS/name
        if path.is_dir() and name not in dependencies:
            print(json.dumps({'stage':'dependency_hash','toolpack':name}),flush=True)
            dependencies[name]={'path':str(path),'source': 'existing verified optional toolpack','hash':tree_hash(path)}
    cases,families=[],[]
    for spec in specs:
        family={key:value for key,value in spec.items() if key!='sources'}
        family['source_files']=[{'relative':('projects/'+spec['family_id']+'/'+name),'sha256':hashlib.sha256(content.encode()).hexdigest()} for name,content in spec['sources'].items()]
        family['source_sha256']=hashlib.sha256(json.dumps(family['source_files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        family['compilations']=[]
        folder=destination/'projects'/spec['family_id']
        for debug in (False,True):
            try:
                compiled,record=compile_family(spec,folder,debug,registry,rascal)
                family['compilations'].append(record)
                for signature in ('FWS','CWS','ZWS'):
                    relative='swf/'+spec['family_id']+'/'+('debug' if debug else 'release')+'.'+signature.lower()+'.swf'
                    variant=compression_variant(compiled.read_bytes(),signature);write_new(destination/relative,variant)
                    inventory=build_inventory(variant)
                    cases.append({'relative':relative,'family_id':spec['family_id'],'split':spec['split'],'language':spec['language'],
                                  'compiler':spec['compiler'],'debug':debug,'compression':signature,'sha256':hashlib.sha256(variant).hexdigest(),
                                  'bytes':len(variant),'source_sha256':family['source_sha256'],'source_license':'CC0-1.0',
                                  'expected_trace':spec['expected_trace'],'application_classes':spec.get('application_classes',[]),
                                  'canonical_inventory_sha256':inventory['sha256'],'scope_denominators':inventory['scope_denominators'],
                                  'quality_dimensions':{'compilation':'original_compiled','behavior':'not_run','source_fidelity':'known_authored_source'}})
            except (OSError,ValueError) as error:
                family['compilations'].append({'status':'unavailable_or_failed','compiler':spec['compiler'],'debug':debug,'diagnostic':str(error)})
            state={'families':families+[family],'cases':cases,'sealed':False,'dependencies':dependencies}
            (destination/'build-state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
            print(json.dumps({'family':spec['family_id'],'debug':debug,'status':family['compilations'][-1]['status']}),flush=True)
        family['compiled_variants']=sum(c['family_id']==spec['family_id'] for c in cases)
        families.append(family)
    manifest={'schema_version':2,'corpus_id':'parity-20261014','edition_id':destination.name,
              'origin_manifest_sha256':previous.get('origin_manifest_sha256'),
              'immutable':True,'source_license':'CC0-1.0',
              'family_split_policy':'fixed independent project families; compression/debug variants cannot cross splits',
              'family_counts':{'development':6,'validation':3,'holdout':3},'application_dependency_weighting':'equal family weight; app and dependency scopes separately',
              'families':families,'cases':cases,'dependencies':dependencies,'complete':len(cases)==72,
              'retired_families':[{'family_id':'holdout-as3-independent','status':'retired_exposed_holdout',
                                  'reason':'native source observed before implementation freeze; excluded from active denominators',
                                  'preserved_sources':'projects/holdout-as3-independent','preserved_variants':'swf/holdout-as3-independent',
                                  'evidence':'reports/parity-implementation-20261014/authored-native-correctness.json'}],
              'claims':{'representative_of_all_flash':False,'parity':False,'behavior_verified':False}}
    if not manifest['complete']:
        manifest.update(status='incomplete_unsealed',immutable=False)
        (destination/'build-state.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        return manifest
    encoded=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8');write_new(manifest_path,encoded)
    write_new(destination/'manifest.sha256',(hashlib.sha256(encoded).hexdigest()+'\n').encode())
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=paths.CORPUS/'parity-20261014')
    parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--rascal-command-json',type=Path)
    args=parser.parse_args();rascal=json.loads(args.rascal_command_json.read_text()) if args.rascal_command_json else None
    manifest=build(args.output,prepare_only=args.prepare_only,rascal=rascal)
    print(json.dumps({'output':str(args.output),'status':manifest.get('status','sealed'),'cases':len(manifest.get('cases',[])), 'complete':manifest.get('complete')}))


if __name__=='__main__': main()
