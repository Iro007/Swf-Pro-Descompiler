import {ReactNode,useId,useState} from 'react';
import {AlertTriangle,FolderOpen} from 'lucide-react';
import * as api from '../api';
import {SuitePickKind} from '../types';
import {problem} from './util';

/** Text field with a native file/folder chooser beside it. */
export function PathField({label,value,onChange,kind,disabled,placeholder}:{label:string;value:string;onChange:(value:string)=>void;kind:SuitePickKind;disabled:boolean;placeholder?:string}){
  const id=useId();
  const [error,setError]=useState('');
  const [picking,setPicking]=useState(false);
  const browse=async()=>{
    setPicking(true);setError('');
    try{const path=await api.suitePick(kind);if(path)onChange(path);}
    catch(failure){setError(problem(failure));}
    finally{setPicking(false);}
  };
  return <div className="field">
    <label htmlFor={id}><span>{label}</span></label>
    <div className="path-row">
      <input id={id} value={value} disabled={disabled} placeholder={placeholder} onChange={event=>onChange(event.target.value)}/>
      <button type="button" className="tool-button" aria-label={`Elegir ${label.toLowerCase()}`} disabled={disabled||picking||!api.native} onClick={()=>void browse()}><FolderOpen size={14}/>{picking?'Eligiendo…':'Elegir'}</button>
    </div>
    {error?<p className="panel-error" role="alert">{error}</p>:null}
  </div>;
}

export function Field({label,children}:{label:string;children:ReactNode}){return <label className="field"><span>{label}</span>{children}</label>;}

export function DiagnosticList({items}:{items:unknown[]}){
  return items.length>0?<div className="diagnostic-list" role="status">{items.map((item,index)=><div key={index}><AlertTriangle size={14}/><span>{problem(item)}</span></div>)}</div>:null;
}
