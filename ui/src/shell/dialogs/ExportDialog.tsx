import {useState} from 'react';
import {ArrowDownToLine} from 'lucide-react';
import * as api from '../../api';
import {SuiteExportResult} from '../../types';
import Dialog from '../../components/Dialog';
import {displayEngineName} from '../../displayNames';
import {DiagnosticList,Field,PathField} from '../fields';
import {Session} from '../useSession';

const formats=[['resource-bundle','Paquete de recursos'],['scripts','Fuentes y P-code'],['xml','SWF XML'],['fla','Adobe FLA'],['xfl','Adobe XFL']] as const;

/** Export the working document as a resource bundle, scripts, SWF XML or an Animate document. */
export default function ExportDialog({session,onClose}:{session:Session;onClose:()=>void}){
  const {catalog,busy,state}=session;
  const [format,setFormat]=useState<string>('resource-bundle');
  const [engine,setEngine]=useState('auto');
  const [destination,setDestination]=useState('');
  const [result,setResult]=useState<SuiteExportResult|null>(null);
  const exporters=catalog?.engines.filter(item=>item.available&&item.operations.includes(format))??[];
  const ready=engine==='auto'?exporters.length>0:exporters.some(item=>item.id===engine);
  const disabled=!!busy||!api.native;
  const file=['fla','xml'].includes(format);

  const run=async()=>{
    if(!state||!ready||!destination.trim())return;
    const next=await session.run('Exportando documento…',()=>api.suiteRpc<SuiteExportResult>('export',{format,path:destination.trim(),engine}));
    if(next){setResult(next);session.notify(`Exportación finalizada: ${next.path}`);}
  };

  return <Dialog title="Exportar" onClose={onClose} size="md">
    <p className="panel-muted">Exporta la copia de trabajo. El archivo original nunca se sobrescribe y el destino debe ser nuevo.</p>
    <Field label="Formato">
      <select aria-label="Formato de exportación" value={format} disabled={disabled} onChange={event=>{setFormat(event.target.value);setEngine('auto');}}>
        {formats.map(([id,label])=><option key={id} value={id}>{label}</option>)}
      </select>
    </Field>
    <Field label="Motor">
      <select aria-label="Motor de exportación" value={engine} disabled={disabled} onChange={event=>setEngine(event.target.value)}>
        <option value="auto">Automático</option>
        {exporters.map(item=><option key={item.id} value={item.id}>{displayEngineName(item.id,item.name)}</option>)}
      </select>
    </Field>
    <PathField label="Destino de exportación" value={destination} onChange={setDestination} kind={file?'output':'folder'} disabled={disabled} placeholder={file?'Archivo de destino':'Carpeta de destino'}/>
    <div className="button-row">
      <button type="button" className="tool-button primary" disabled={disabled||!state||!ready||!destination.trim()} onClick={()=>void run()}><ArrowDownToLine size={14}/>Exportar</button>
    </div>
    {!ready?<p className="panel-error">No hay un motor configurado que anuncie este formato.</p>:null}
    {result?<div className="result-card"><p className="result-path">{result.path}</p><p className="panel-muted">Motor: {displayEngineName(result.engine)}</p><DiagnosticList items={result.diagnostics??[]}/></div>:null}
  </Dialog>;
}
