"""Independent execution of TRUSTED test sources using their JS-compatible subset.

Only type annotations are stripped; closures/with/control flow execute in V8.
This is not an ActionScript runtime and must never run uploaded SWF content.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

NODE = shutil.which("node") or (str(Path("C:/Program Files/nodejs/node.exe")) if Path("C:/Program Files/nodejs/node.exe").exists() else None)


def run_trusted_subset(source, arguments=(), variables=None):
    parts = re.split(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', source)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r':\s*(?:String|int|uint|Number|Boolean|Object|Function|\*)(?![\w$])', '', parts[i])
    script = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const traces = [];
const context = {trace: (...args) => traces.push(...args), int: v=>v|0, uint: v=>v>>>0,
    Function: v=>v, ...input.variables, args: input.args};
const value = vm.runInNewContext('(function(){' + input.source + '\n}).apply(null,args)', context,
    {timeout:1000, contextCodeGeneration:{strings:false, wasm:false}});
process.stdout.write(JSON.stringify({traces, value: value === undefined ? null : value, root: context._root}));
'''
    result = subprocess.run([NODE, "-e", script], input=json.dumps(dict(source="".join(parts), args=list(arguments), variables=variables or {})), text=True, capture_output=True, timeout=5)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)
