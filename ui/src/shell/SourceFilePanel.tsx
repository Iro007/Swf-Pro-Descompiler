import {useEffect,useRef,useState} from 'react';
import {Save} from 'lucide-react';
import * as api from '../api';
import {SuiteSourceDocument,SuiteState,TreeNode} from '../types';
import {displayEngineName} from '../displayNames';
import CodeEditor,{CodeLanguage} from './CodeEditor';
import {Session} from './useSession';
import {problem,shortRevision} from './util';

interface Draft {text:string;revision:string;sourceSha:string}
const languageOf=(name:string):CodeLanguage=>/\.(as|hx|mxml)$/i.test(name)?'actionscript':'text';

/** An authored source file kept in the project (ActionScript, Haxe, Project.xml): edit and save; recompile from Herramientas. */
export default function SourceFilePanel({node,session}:{node:TreeNode;session:Session}){
  const [document,setDocument]=useState<SuiteSourceDocument|null>(null);
  const [error,setError]=useState('');
  const [generation,setGeneration]=useState(0);
  const epoch=useRef(0);
  const {state,documentKey,drafts,track}=session;
  const id=node.source_id??'';
  const revision=state?.revision;
  const sourceRevision=state?.source_revision;

  useEffect(()=>{
    if(!id||revision===undefined)return;
    const ticket=++epoch.current;
    track(api.suiteRpc<SuiteSourceDocument>('source_files',{id})).then(next=>{if(ticket===epoch.current){setDocument(next);setError('');}})
      .catch(failure=>{if(ticket===epoch.current){setDocument(null);setError(problem(failure));}});
    return()=>{epoch.current++;};
  },[id,revision,sourceRevision,track]);

  const draftKey=`source|${documentKey}|${id}`;
  const draft=drafts.get<Draft>(draftKey);
  const text=draft?.text??document?.source??'';
  const canEdit=!!document?.editable&&!!document.source_sha256;
  const filename=document?.filename??node.label;
  const save=async()=>{
    if(!state||!draft||!canEdit)return;
    const next=await session.apply('Guardando fuente…',()=>api.suiteRpc<SuiteState>('source_edit',{id,source:draft.text,base_revision:draft.revision,base_source_sha256:draft.sourceSha}),
      'Fuente guardada. Recompila el proyecto (Herramientas → Compilar) para actualizar el SWF.');
    if(next)drafts.set(draftKey,undefined);
  };
  if(error)return <p className="panel-error" role="alert">{error}</p>;
  return <div className="script-panel source-file-panel" data-testid="source-file-panel">
    <div className="panel-toolbar">
      <span className="toolbar-text"><strong>{filename}</strong>{document?.engine?` · ${displayEngineName(document.engine)}`:''}</span>
      <span className="toolbar-gap"/>
      <span className="toolbar-text dim">{draft?`Borrador · revisión base ${shortRevision(draft.revision)}`:canEdit?'Fuente editable':'Solo lectura'}</span>
      <button type="button" className="tool-button" disabled={!draft||!!session.busy} onClick={()=>{drafts.set(draftKey,undefined);setGeneration(value=>value+1);}}>Descartar</button>
      <button type="button" className="tool-button primary" disabled={!draft||!canEdit||!!session.busy||text===document?.source} onClick={()=>void save()}><Save size={14}/>Guardar fuente</button>
    </div>
    {state?.source_pending?<p className="panel-note" role="status">Hay fuentes guardadas que todavía no se han recompilado en el SWF.</p>:null}
    {draft&&document&&(draft.revision!==revision||draft.sourceSha!==document.source_sha256)?<p className="panel-error">La fuente o el SWF cambió desde que comenzaste este borrador; el motor comprobará ambas revisiones.</p>:null}
    <div className="single-pane">
      <CodeEditor value={document?text:''} docKey={`file|${id}|${documentKey}|${document?.source_sha256}`} syncKey={`${document?.source_sha256}|${generation}`} language={languageOf(filename)} readOnly={!canEdit} label={`Fuente del proyecto ${filename}`} testId="source-file"
        placeholder={document?undefined:'Leyendo fuente…'}
        onChange={value=>{if(document&&canEdit){if(value===document.source)drafts.set(draftKey,undefined);else drafts.set<Draft>(draftKey,{text:value,revision:draft?.revision??revision??'',sourceSha:draft?.sourceSha??document.source_sha256});}}}/>
    </div>
  </div>;
}
