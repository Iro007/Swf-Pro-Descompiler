import fs from 'node:fs';
const [rustFile,pythonFile,output]=process.argv.slice(2);
if(!output)throw new Error('Uso: node tools/compare-audit.mjs rust.json python.json output.json');
const rust=JSON.parse(fs.readFileSync(rustFile,'utf8')),python=JSON.parse(fs.readFileSync(pythonFile,'utf8'));
const key=(u)=>`${JSON.stringify(u.tag_path)}:${u.language==='avm1'?'as2':u.language==='avm2'?'as3':u.language}:${u.method_index??''}:${u.sha256??u.bytecode_sha256??''}`;
const cases=rust.cases.map(r=>{
  const p=python.cases.find(c=>c.relative===r.relative);
  const pyUnits=p?.engines?.find(e=>e.engine==='native')?.units??[];
  const rustUnits=r.result.units_metadata??[];
  // Generation diagnostics do not remove inventoried bytecode from coverage.
  // Class outlines are navigational documents, not bytecode units.
  const available=pyUnits.filter(u=>u.kind!=='outline'&&u.bytecode_sha256),rustAvailable=rustUnits.filter(u=>u.kind!=='outline'&&u.sha256);
  const pyMap=new Map(available.map(u=>[key(u),u])),rustMap=new Map(rustAvailable.map(u=>[key(u),u]));
  const hashMatches=(r.actual_sha256??r.result.info?.sha256)===p?.sha256;
  return {relative:r.relative,hash_matches:hashMatches,rust_status:r.result.status??'ok',rust_units:rustUnits.length,python_units:pyUnits.length,matched:hashMatches?[...pyMap.keys()].filter(k=>rustMap.has(k)).length:0,rust_only:hashMatches?rustAvailable.filter(u=>!pyMap.has(key(u))).map(u=>u.id):[],python_only:hashMatches?available.filter(u=>!rustMap.has(key(u))).map(u=>u.document_id):[]};
});
const summary={files:cases.length,hash_matches:cases.filter(c=>c.hash_matches).length,matched_units:cases.reduce((s,c)=>s+c.matched,0),rust_units:cases.reduce((s,c)=>s+c.rust_units,0),python_units:cases.reduce((s,c)=>s+c.python_units,0),rust_only:cases.reduce((s,c)=>s+c.rust_only.length,0),python_only:cases.reduce((s,c)=>s+c.python_only.length,0)};
fs.writeFileSync(output,JSON.stringify({scope:'Inventory comparison against historical Python report with matching SWF hashes; not semantic parity or a speed comparison',summary,cases},null,2));console.log(summary);

