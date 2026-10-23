import {useEffect,useState} from 'react';
import {Loader2} from 'lucide-react';
import * as api from '../api';
import {SuiteDecompile} from '../types';
import {displayEngineName} from '../displayNames';
import Dialog from '../components/Dialog';
import CodeEditor from './CodeEditor';
import {problem} from './util';

type Result=SuiteDecompile&{status?:string};

/** Output of every engine for one unit, side by side. Availability of source does not prove equivalent behaviour. */
export default function CompareDialog({unitId,name,onClose}:{unitId:string;name:string;onClose:()=>void}){
  const [results,setResults]=useState<Result[]|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    let current=true;
    api.suiteRpc<{results:Result[]}>('compare',{unit_id:unitId}).then(next=>{if(current)setResults(next.results);}).catch(failure=>{if(current)setError(problem(failure));});
    return()=>{current=false;};
  },[unitId]);
  return <Dialog title={`Comparar motores · ${name}`} onClose={onClose} size="xl">
    <p className="panel-muted">Cada motor conserva su propia salida y diagnóstico. Que exista fuente no demuestra que el comportamiento sea equivalente.</p>
    {error?<p className="panel-error" role="alert">{error}</p>:null}
    {!results&&!error?<p className="panel-muted"><Loader2 size={14} className="spin"/> Consultando los motores…</p>:null}
    <div className="compare-grid">
      {results?.map((result,index)=>{
        const diagnostics=(result.diagnostics??[]).map(problem);
        const text=result.engine==='jpexs'&&result.document_source!=null?result.document_source:result.source;
        return <article key={`${result.engine}-${index}`} className="compare-card">
          <header><h3>{displayEngineName(result.engine)}</h3><span className={`badge ${text!=null?'good':''}`}>{text!=null?'Fuente disponible':'P-code / diagnóstico'}</span></header>
          {diagnostics.length?<p className="panel-note">{diagnostics.join(' · ')}</p>:null}
          <div className="compare-code"><CodeEditor value={text??result.pcode??'// Sin salida disponible.'} docKey={`compare-${result.engine}`} syncKey="static" language={text!=null?'actionscript':'pcode'} readOnly label={`Salida de ${displayEngineName(result.engine)}`}/></div>
        </article>;
      })}
    </div>
  </Dialog>;
}
