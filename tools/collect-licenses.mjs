import fs from 'node:fs';
import path from 'node:path';
const output=path.resolve('dist/licenses');fs.mkdirSync(output,{recursive:true});
const inventory=[];
function collect(name,version,license,folder){
  const destination=path.join(output,`${name.replace(/[^a-z0-9_.-]/gi,'_')}-${version}`);fs.mkdirSync(destination,{recursive:true});
  const files=[];
  for(const entry of fs.readdirSync(folder,{withFileTypes:true}))if(entry.isFile()&&/^(licen[cs]e|copying|copyright|notice)([._-]|$)/i.test(entry.name)){fs.copyFileSync(path.join(folder,entry.name),path.join(destination,entry.name));files.push(entry.name);}
  inventory.push({name,version,license,texts:files});
}
const metadata=JSON.parse(fs.readFileSync('reports/cargo-metadata.json','utf8'));
for(const item of metadata.packages)collect(item.name,item.version,item.license,path.dirname(item.manifest_path));
const modules=path.resolve('ui/node_modules');
for(const entry of fs.readdirSync(modules,{withFileTypes:true})){
  if(!entry.isDirectory()||entry.name.startsWith('.'))continue;
  const folders=entry.name.startsWith('@')?fs.readdirSync(path.join(modules,entry.name)).map(n=>path.join(modules,entry.name,n)):[path.join(modules,entry.name)];
  for(const folder of folders){const manifest=path.join(folder,'package.json');if(fs.existsSync(manifest)){const item=JSON.parse(fs.readFileSync(manifest,'utf8'));collect(item.name,item.version,item.license??'SEE LICENSE',folder);}}
}
const ruffle=path.resolve('vendor/third_party/ruffle');if(fs.existsSync(path.join(ruffle,'LICENSE.md'))){fs.mkdirSync(path.join(output,'ruffle'),{recursive:true});for(const name of ['LICENSE.md','LICENSE_MIT','LICENSE_APACHE'])if(fs.existsSync(path.join(ruffle,name)))fs.copyFileSync(path.join(ruffle,name),path.join(output,'ruffle',name));}
const rustLicenses=path.join(process.env.USERPROFILE,'.rustup/toolchains/1.99.0-x86_64-pc-windows-gnu/share/doc/rust/licenses');
if(fs.existsSync(rustLicenses)){const target=path.join(output,'rust-stdlib');fs.mkdirSync(target,{recursive:true});for(const file of fs.readdirSync(rustLicenses))if(file.endsWith('.txt'))fs.copyFileSync(path.join(rustLicenses,file),path.join(target,file));}
const compiler=path.resolve('tools/bootstrap/mingw/mingw64');
for(const relative of ['share/doc/gcc/COPYING','share/doc/gcc/COPYING.LIB','share/doc/gcc/COPYING.RUNTIME','licenses/COPYING','licenses/COPYING.RUNTIME']){const file=path.join(compiler,relative);if(fs.existsSync(file)){const target=path.join(output,'gcc-runtime');fs.mkdirSync(target,{recursive:true});fs.copyFileSync(file,path.join(target,path.basename(file)));}}
fs.writeFileSync(path.join(output,'inventory.json'),JSON.stringify(inventory,null,2));console.log(`Avisos recogidos: ${inventory.length} paquetes`);

