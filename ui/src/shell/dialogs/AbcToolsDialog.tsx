import {useState} from 'react';
import {ArrowDownToLine,Check} from 'lucide-react';
import * as api from '../../api';
import {SuiteState} from '../../types';
import Dialog from '../../components/Dialog';
import {PathField} from '../fields';
import {Session} from '../useSession';

interface AssemblyExport {path:string;engine:string;artifacts:{file:string;sha256:string}[];log?:string;mapping:string;behavior:string}

/** RABCDAsm round trip for the ABC block that holds the selected class or method. */
export default function AbcToolsDialog({unitId,name,session,onClose}:{unitId:string;name:string;session:Session;onClose:()=>void}){
  const tool=session.catalog?.engines.find(item=>item.id==='rabcdasm');
  const [destination,setDestination]=useState('');
  const [entry,setEntry]=useState('');
  const [exported,setExported]=useState<AssemblyExport|null>(null);
  const [log,setLog]=useState('');
  const disabled=!!session.busy||!api.native;
  const available=!!tool?.available&&!!session.state;

  const disassemble=async()=>{
    const result=await session.run('Exportando ABC con RABCDAsm…',()=>api.suiteRpc<AssemblyExport>('tool_operation',{id:'rabcdasm',operation:'disassemble',unit_id:unitId,path:destination.trim()}));
    if(result)setExported(result);
  };
  const assemble=async()=>{
    if(!session.state)return;
    const state=session.state;
    const next=await session.apply('Ensamblando y validando ABC…',async()=>{
      const result=await api.suiteRpc<SuiteState&{engine:string;log:string}>('tool_operation',{id:'rabcdasm',operation:'assemble',unit_id:unitId,entry:entry.trim(),base_revision:state.revision});
      setLog(result.log);return result;
    },'ABC ensamblado, validado y aplicado a la copia de trabajo.');
    if(next)setEntry('');
  };

  return <Dialog title={`Ensamblado ABC con RABCDAsm · ${name}`} onClose={onClose} size="lg">
    <p className="panel-muted">El elemento seleccionado identifica el bloque ABC completo. Exportar genera su estructura .asasm; importar reemplaza ese bloque, conserva el resto del SWF y valida la copia de trabajo.</p>
    {!available?<p className="panel-error">Configura RABCDAsm en Herramientas → Motores para habilitar estas operaciones.</p>:null}
    <div className="form-grid">
      <section className="card">
        <h3>Exportar</h3>
        <PathField label="Carpeta para el ABC desensamblado" value={destination} onChange={setDestination} kind="folder" disabled={disabled}/>
        <button type="button" className="tool-button" disabled={disabled||!available||!tool?.operations.includes('disassemble')||!destination.trim()} onClick={()=>void disassemble()}><ArrowDownToLine size={14}/>Exportar ABC a .asasm</button>
        {exported?<><p className="result-path">{exported.path}</p><p className="panel-muted small">{exported.artifacts.length} archivos generados. Ejecución y equivalencia semántica sin verificar.</p>
          <details><summary>Archivos y hashes</summary><pre className="log">{JSON.stringify(exported.artifacts,null,2)}</pre></details></>:null}
      </section>
      <section className="card">
        <h3>Importar</h3>
        <PathField label="Archivo principal .asasm" value={entry} onChange={setEntry} kind="source" disabled={disabled}/>
        <button type="button" className="tool-button primary" disabled={disabled||!available||!tool?.operations.includes('assemble')||!entry.trim()} onClick={()=>void assemble()}><Check size={14}/>Importar ABC y validar</button>
        {log?<pre className="log">{log}</pre>:null}
      </section>
    </div>
  </Dialog>;
}
