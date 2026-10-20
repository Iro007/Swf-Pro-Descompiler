"""Real compiler/module smoke on authored, local fixtures. No Flash execution."""
from pathlib import Path
import json
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf.universal import UniversalSuite


def main():
    destination = PROJECT / 'reports/suite-integration'
    destination.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'gui_started': False, 'browser_started': False, 'behavior_verified': False, 'parity': False, 'superiority': False}
    with tempfile.TemporaryDirectory(prefix='suite-check-') as temporary:
        folder = Path(temporary)
        suite = UniversalSuite(folder / 'state', folder / 'tools.json')
        jobs = []
        haxe = folder / 'Main.hx'
        haxe.write_text('class Main { static function main() { var sprite = new flash.display.Sprite(); sprite.graphics.beginFill(0x11aa33); sprite.graphics.drawRect(0, 0, 40, 40); sprite.graphics.endFill(); flash.Lib.current.addChild(sprite); trace("SUITE_HAXE_OK"); } }')
        jobs.append(('haxe', haxe))
        flex = folder / 'Hello.as'
        flex.write_text('package { import flash.display.Sprite; public class Hello extends Sprite { public function Hello() { super(); graphics.beginFill(0x33aa11); graphics.drawRect(0,0,40,40); graphics.endFill(); trace("SUITE_AS3_OK"); } } }')
        jobs.append(('as3-flex', flex))
        flixel = folder / 'flixel-game'
        (flixel / 'source').mkdir(parents=True)
        (flixel / 'Project.xml').write_text('''<project><meta title="Suite fixture" package="suite.fixture" version="1.0.0" company="Local"/><app main="Main" file="SuiteFixture" path="export"/><window width="640" height="480" fps="30"/><source path="source"/><haxelib name="flixel" version="6.2.0"/><haxedef name="FLX_NO_GAMEPAD"/><haxedef name="FLX_NO_MOUSE_ADVANCED"/></project>''')
        (flixel / 'source/Main.hx').write_text('import flixel.FlxGame; import flash.display.Sprite; class Main extends Sprite { public function new() { super(); addChild(new FlxGame(640, 480, PlayState)); } }')
        (flixel / 'source/PlayState.hx').write_text('import flixel.FlxState; import flixel.FlxSprite; import flixel.text.FlxText; class PlayState extends FlxState { override public function create() { super.create(); add(new FlxSprite(20,20).makeGraphic(40,40,0xff33aa11)); add(new FlxText(20,80,400,"Suite HaxeFlixel",16)); trace("SUITE_FLIXEL_OK"); } }')
        jobs.append(('haxeflixel', flixel / 'Project.xml'))
        for profile, entry in jobs:
            output = destination / (profile + '-fixture.swf')
            if output.exists():
                output = destination / (profile + '-' + folder.name + '.swf')
            try:
                rejected = destination / ('rejected-' + profile + '-' + folder.name + '.swf')
                result = suite.compile_project({'profile': profile, 'entry': str(entry), 'output': str(output), 'diagnostic_output': str(rejected)})
                units = suite.units()
                selected = next((u for u in units if u.language == 'as3' and u.name.endswith('main')), units[-1])
                comparison = suite.compare({'unit_id': selected.document_id})
                report['checks'].append({'profile': profile, 'passed': result['validation']['valid'], 'output': str(output), 'sha256': result['revision'], 'scripts': result['script_count'], 'log': result['log'], 'comparison': comparison})
                if profile in ('haxe', 'haxeflixel'):
                    document = suite.dispatch('source_files', {'id':'project:entry'})
                    changed_source = document['source'].replace('SUITE_HAXE_OK', 'SUITE_HAXE_SAVED_OK') if profile == 'haxe' else document['source'].replace('Suite fixture', 'Suite saved fixture')
                    suite.source_edit({'id':'project:entry','source':changed_source,'base_revision':suite.state()['revision'],
                                       'base_source_sha256':document['source_sha256']})
                    saved_output = destination / (profile + '-saved-' + folder.name + '.swf')
                    rebuilt = suite.compile_saved({'output':str(saved_output)})
                    assert not rebuilt['source_pending'] and entry.read_text()!=changed_source
                    report['checks'].append({'profile':profile+'-saved-source','passed':rebuilt['validation']['valid'],
                                             'output':str(saved_output),'sha256':rebuilt['revision'],'scripts':rebuilt['script_count'],
                                             'external_source_unchanged':True,'log':rebuilt['log']})
            except Exception as error:
                report['checks'].append({'profile': profile, 'passed': False, 'error': str(error)})
            (destination / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in report['checks'][-1].items() if k != 'comparison'}), flush=True)
        suite.close()
    report['passed'] = all(c['passed'] for c in report['checks'])
    (destination / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
