import {AlertTriangle,Check,Loader2,X} from 'lucide-react';
import {Session} from './useSession';
import {shortRevision} from './util';

const compression:Record<string,string>={FWS:'sin compresión',CWS:'zlib',ZWS:'LZMA'};

/** Bottom line: what the suite is doing, which file is open and how it differs from the original. */
export default function StatusBar({session,engine}:{session:Session;engine:string}){
  const {state,busy,reading,notice,drafts}=session;
  const validation=state?.validation;
  return <footer className="statusbar" aria-label="Estado">
    {notice?<div className={`toast ${notice.error?'error':'ok'}`} role={notice.error?'alert':'status'}>
      {notice.error?<AlertTriangle size={14}/>:<Check size={14}/>}<span>{notice.text}</span>
      <button type="button" className="icon-button small" aria-label="Cerrar aviso" onClick={session.dismissNotice}><X size={13}/></button>
    </div>:null}
    <span className="status-item activity" data-testid="status-activity">
      {busy?<><Loader2 size={13} className="spin"/>{busy}<button type="button" className="link-button" onClick={()=>void session.cancel()}>Cancelar</button></>
        :reading?<><Loader2 size={13} className="spin"/>Leyendo…</>:<><span className="dot ok"/>Listo</>}
    </span>
    {state?<>
      <span className="status-item" title={state.source_path??state.working_path}>{state.filename}</span>
      {state.signature?<span className="status-item">{state.signature} · SWF {validation?.version??'?'} · {compression[state.signature]??''}</span>:null}
      {validation?.tag_count!==undefined?<span className="status-item">{validation.tag_count.toLocaleString()} tags · {(validation.frame_count??state.frame_count??0).toLocaleString()} cuadros</span>:null}
      <span className="status-item">{state.script_count.toLocaleString()} unidades de código</span>
    </>:<span className="status-item dim">Sin archivo abierto</span>}
    <span className="status-spacer"/>
    {drafts.count>0?<span className="status-item warn">{drafts.count} borrador{drafts.count===1?'':'es'}</span>:null}
    {state?<span className="status-item" title={`Revisión ${state.revision}`}>
      <span className={`dot ${state.source_pending?'warn':state.dirty?'dirty':'clean'}`}/>{state.source_pending?'Fuentes pendientes':state.dirty?'Copia modificada':'Sin cambios'} · {shortRevision(state.revision)}</span>:null}
    <span className="status-item" title="Motor de descompilación preferido">Motor: {engine}</span>
  </footer>;
}
