import {useState} from 'react';
import {Play} from 'lucide-react';
import * as api from '../../api';
import {SuiteCompileResult,SuiteProfile} from '../../types';
import Dialog from '../../components/Dialog';
import {compileProfiles} from '../../compileProfiles';
import {displayEngineName} from '../../displayNames';
import {DiagnosticList,Field,PathField} from '../fields';
import {Session} from '../useSession';

const logText=(log:string|string[])=>Array.isArray(log)?log.join('\n'):log;

function Result({result}:{result:SuiteCompileResult}){
  const valid=result.validation?.valid;
  return <div className="result-card">
    <p className="result-path">{result.path}</p>
    <span className={`badge ${valid?'good':''}`}>{displayEngineName(result.engine)} · {valid?'SWF válido':result.validation?'Validación con errores':'Consulta la validación del motor'}</span>
    <DiagnosticList items={[...(result.validation?.errors??[]),...(result.validation?.warnings??[]),...(result.diagnostics??[])]}/>
    {result.log?<pre className="log">{logText(result.log)}</pre>:null}
  </div>;
}

/** Compile ActionScript, Haxe or HaxeFlixel sources into a new SWF, or rebuild the sources kept in the project. */
export default function CompileDialog({session,hasSavedSources,onClose}:{session:Session;hasSavedSources:boolean;onClose:()=>void}){
  const {catalog,busy,state}=session;
  const [profile,setProfile]=useState<SuiteProfile>('auto-as3');
  const [entry,setEntry]=useState('');
  const [output,setOutput]=useState('');
  const [mainClass,setMainClass]=useState('');
  const [debug,setDebug]=useState(false);
  const [result,setResult]=useState<SuiteCompileResult|null>(null);
  const [savedProfile,setSavedProfile]=useState<SuiteProfile|''>('');
  const [savedMain,setSavedMain]=useState('');
  const [savedOutput,setSavedOutput]=useState('');
  const [rebuilt,setRebuilt]=useState<SuiteCompileResult|null>(null);
  const available=compileProfiles.filter(item=>catalog?.engines.some(tool=>tool.id===item.engine&&tool.available&&tool.operations.includes('compile_project')));
  const ready=available.some(item=>item.id===profile);
  const disabled=!!busy||!api.native;
  const supportsSaved=catalog?.engines.some(tool=>tool.available&&tool.operations.includes('compile_saved'))??false;

  const compile=async()=>{
    if(!ready||!entry.trim()||!output.trim())return;
    const next=await session.run('Compilando proyecto…',async()=>{
      const compiled=await api.suiteRpc<SuiteCompileResult>('compile_project',{profile,entry:entry.trim(),output:output.trim(),...(mainClass.trim()?{main_class:mainClass.trim()}:{}),debug});
      await session.replaceDocument(compiled);
      return compiled;
    });
    if(next){setResult(next);session.notify(`Compilación finalizada: ${next.path}`,next.validation?.valid===false);}
  };
  const rebuild=async()=>{
    if(!savedOutput.trim())return;
    await session.apply('Recompilando fuentes guardadas…',async()=>{
      const compiled=await api.suiteRpc<SuiteCompileResult>('compile_saved',{output:savedOutput.trim(),...(savedProfile?{profile:savedProfile}:{}),...(savedMain.trim()?{main_class:savedMain.trim()}:{})});
      setRebuilt(compiled);return compiled;
    },'Proyecto recompilado desde sus fuentes guardadas.');
  };

  return <Dialog title="Compilar hacia SWF" onClose={onClose} size="lg">
    <p className="panel-muted">Selecciona las fuentes originales o el proyecto. El resultado se valida antes de abrirlo en esta ventana. Un SWF descompilado no recupera el proyecto .hx original: Haxe y HaxeFlixel necesitan sus fuentes y dependencias.</p>
    <div className="form-grid">
      <section className="card">
        <Field label="Perfil de compilación">
          <select aria-label="Perfil de compilación" value={profile} disabled={disabled} onChange={event=>setProfile(event.target.value as SuiteProfile)}>
            {compileProfiles.map(item=>{const ok=available.some(candidate=>candidate.id===item.id);return <option key={item.id} value={item.id} disabled={!ok}>{item.label}{ok?'':' · herramienta pendiente'}</option>;})}
          </select>
        </Field>
        <p className="panel-muted small">{compileProfiles.find(item=>item.id===profile)?.hint}</p>
        <PathField label="Fuente o proyecto de entrada" value={entry} onChange={setEntry} kind="source" disabled={disabled} placeholder={profile==='haxeflixel'?'Project.xml':profile==='haxe'?'build.hxml o Main.hx':'Archivo .as'}/>
        <PathField label="Archivo SWF de salida" value={output} onChange={setOutput} kind="output" disabled={disabled} placeholder="Archivo .swf de destino"/>
        <Field label="Clase principal (opcional)"><input value={mainClass} disabled={disabled} onChange={event=>setMainClass(event.target.value)} placeholder="Main"/></Field>
        <label className="check-line"><input type="checkbox" checked={debug} disabled={disabled} onChange={event=>setDebug(event.target.checked)}/>Compilar con información de depuración</label>
        <button type="button" className="tool-button primary" disabled={disabled||!ready||!entry.trim()||!output.trim()} onClick={()=>void compile()}><Play size={14}/>Compilar y validar</button>
        {!ready?<p className="panel-error">Configura la herramienta de este perfil en Herramientas → Motores para habilitar la compilación.</p>:null}
      </section>
      <section className="card">
        <h3>Resultado</h3>
        {result?<Result result={result}/>:<p className="panel-muted">El log, el motor utilizado y los diagnósticos aparecerán después de una compilación real.</p>}
      </section>
    </div>
    {hasSavedSources?<section className="card spaced">
      <h3>Recompilar las fuentes guardadas del proyecto</h3>
      <p className="panel-muted">Utiliza el perfil conservado del proyecto o elige otro compatible. El resultado se guarda en un SWF nuevo y actualiza la copia de trabajo.{state?.source_pending?' Hay fuentes guardadas pendientes de recompilar.':''}</p>
      <div className="form-grid">
        <div>
          <Field label="Perfil de recompilación">
            <select aria-label="Perfil de recompilación" value={savedProfile} disabled={disabled} onChange={event=>setSavedProfile(event.target.value as SuiteProfile|'')}>
              <option value="">Perfil guardado del proyecto</option>
              {available.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </Field>
          <Field label="Clase principal (opcional)"><input aria-label="Clase principal de recompilación" value={savedMain} disabled={disabled} onChange={event=>setSavedMain(event.target.value)}/></Field>
          <PathField label="Nuevo SWF de salida" value={savedOutput} onChange={setSavedOutput} kind="output" disabled={disabled} placeholder="Archivo .swf de destino"/>
          <button type="button" className="tool-button primary" disabled={disabled||!supportsSaved||!savedOutput.trim()||session.drafts.has('source|')} onClick={()=>void rebuild()}><Play size={14}/>Recompilar proyecto</button>
          {session.drafts.has('source|')?<p className="panel-error">Guarda o descarta los borradores de las fuentes antes de recompilar: la compilación usa las fuentes ya guardadas.</p>:null}
        </div>
        <div>{rebuilt?<Result result={rebuilt}/>:<p className="panel-muted">El resultado de la recompilación aparecerá aquí.</p>}</div>
      </div>
    </section>:null}
  </Dialog>;
}
