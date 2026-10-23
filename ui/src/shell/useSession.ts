import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import * as api from '../api';
import {SuiteCatalog,SuiteRun,SuiteState,SuiteTagTree} from '../types';
import {problem,readStored,writeStored} from './util';

export interface Notice {text:string;error:boolean}
export interface Recent {path:string;name:string;project:boolean}
const recentsKey='flash-studio.recent.v2';

function loadRecents():Recent[]{
  try{
    const value=JSON.parse(readStored(recentsKey)??'[]');
    return Array.isArray(value)?value.filter((item:Recent)=>item&&typeof item.path==='string'&&typeof item.name==='string').slice(0,8):[];
  }catch{return [];}
}

/** Unsaved text typed in editors; it survives switching nodes and engines. */
export interface DraftStore {get:<T>(key:string)=>T|undefined;set:<T>(key:string,value:T|undefined)=>void;count:number;/** Any draft whose key starts with the prefix? */has:(prefix:string)=>boolean}

/**
 * The one document the window shows: the Python suite session, its tag tree and the engine catalogue.
 * Mutations are exclusive (`run`/`apply`); reads happen in the panels and only report through `track`.
 */
export function useSession(){
  const [state,setState]=useState<SuiteState|null>(null);
  const [tree,setTree]=useState<SuiteTagTree|null>(null);
  const [catalog,setCatalog]=useState<SuiteCatalog|null>(null);
  const [busy,setBusy]=useState('');
  const [reading,setReading]=useState(0);
  const [notice,setNotice]=useState<Notice|null>(null);
  const [documentKey,setDocumentKey]=useState(0);
  const [recents,setRecents]=useState<Recent[]>(loadRecents);
  const [draftVersion,setDraftVersion]=useState(0);
  const drafts=useRef(new Map<string,unknown>());
  const busyRef=useRef(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);

  const notify=useCallback((text:string,error=false)=>setNotice({text,error}),[]);
  const dismissNotice=useCallback(()=>setNotice(null),[]);

  const run:SuiteRun=useCallback(async<T,>(label:string,operation:()=>Promise<T>)=>{
    if(busyRef.current){setNotice({text:'Espera a que termine la operación en curso o cancélala.',error:true});return undefined;}
    busyRef.current=true;setBusy(label);setNotice(null);
    try{return await operation();}
    catch(error){if(mounted.current)setNotice({text:problem(error),error:true});return undefined;}
    finally{busyRef.current=false;if(mounted.current)setBusy('');}
  },[]);

  const track=useCallback(<T,>(promise:Promise<T>):Promise<T>=>{
    setReading(count=>count+1);
    const done=()=>{if(mounted.current)setReading(count=>Math.max(0,count-1));};
    promise.then(done,done);
    return promise;
  },[]);

  const loadTree=useCallback(async()=>{const next=await api.suiteRpc<SuiteTagTree>('tag_tree');if(mounted.current)setTree(next);return next;},[]);
  const refreshCatalog=useCallback(async()=>{
    try{const next=await api.suiteRpc<SuiteCatalog>('engines');if(mounted.current)setCatalog(next);return next;}
    catch(error){if(mounted.current)setNotice({text:problem(error),error:true});return undefined;}
  },[]);
  useEffect(()=>{if(api.native)void refreshCatalog();},[refreshCatalog]);

  /** Adopt a state returned by the suite and refresh the tree that depends on it. */
  const adopt=useCallback(async(next:SuiteState)=>{setState(next);await loadTree();},[loadTree]);

  const apply=useCallback((label:string,operation:()=>Promise<SuiteState>,message?:string)=>run(label,async()=>{
    const next=await operation();
    await adopt(next);
    if(mounted.current)setNotice({text:message??'Cambio validado y aplicado a la copia de trabajo.',error:false});
    return next;
  }),[run,adopt]);

  const remember=useCallback((path:string,name:string,project:boolean)=>{
    setRecents(previous=>{
      const next=[{path,name,project},...previous.filter(item=>item.path!==path)].slice(0,8);
      writeStored(recentsKey,JSON.stringify(next));return next;
    });
  },[]);

  const openPath=useCallback(async(path:string,project=false)=>{
    const opened=await run(project?'Abriendo proyecto…':'Abriendo SWF…',async()=>{
      const next=await api.suiteRpc<SuiteState>(project?'load_project':'open',{path});
      setDocumentKey(key=>key+1);setTree(null);
      drafts.current.clear();setDraftVersion(version=>version+1);
      await adopt(next);
      remember(path,next.filename,project);
      setNotice({text:`${next.filename} · ${next.validation?.tag_count??'?'} tags y ${next.script_count} unidades de código`,error:false});
      return true;
    });
    return opened===true;
  },[run,adopt,remember]);

  const pickAndOpen=useCallback(async(kind:'swf'|'project')=>{
    if(busyRef.current)return;
    let path:string|null|undefined;
    try{path=await api.suitePick(kind);}catch(error){setNotice({text:problem(error),error:true});return;}
    if(path)await openPath(path,kind==='project');
  },[openPath]);

  const close=useCallback(()=>{
    setState(null);setTree(null);setDocumentKey(key=>key+1);drafts.current.clear();setDraftVersion(version=>version+1);setNotice(null);
  },[]);

  /** A compile produced a different program: start over with its state (new drafts namespace, fresh tree). */
  const replaceDocument=useCallback(async(next:SuiteState)=>{
    setDocumentKey(key=>key+1);setTree(null);drafts.current.clear();setDraftVersion(version=>version+1);
    await adopt(next);
  },[adopt]);

  const undo=useCallback(()=>apply('Deshaciendo…',()=>api.suiteRpc<SuiteState>('undo'),'Cambio deshecho.'),[apply]);
  const redo=useCallback(()=>apply('Rehaciendo…',()=>api.suiteRpc<SuiteState>('redo'),'Cambio rehecho.'),[apply]);
  const validate=useCallback(async()=>{
    const result=await run('Validando SWF…',()=>api.suiteRpc<{valid:boolean;errors:unknown[];warnings:unknown[]}>('validate'));
    if(result)setNotice({text:result.valid?`Estructura SWF válida${result.warnings?.length?` · ${result.warnings.length} avisos`:''}.`:`El SWF contiene errores de validación: ${result.errors.map(problem).join('; ')}`,error:!result.valid});
  },[run]);

  const saveProject=useCallback(async()=>{
    if(!state||busyRef.current)return;
    let path:string|null|undefined;
    try{path=await api.suitePick('project_output');}catch(error){setNotice({text:problem(error),error:true});return;}
    if(!path)return;
    const saved=await run('Guardando proyecto…',()=>api.suiteRpc<SuiteState&{path:string}>('save_project',{path}));
    if(saved){remember(saved.path,saved.filename,true);setNotice({text:`Proyecto guardado: ${saved.path}`,error:false});}
  },[state,run,remember]);

  const saveSwf=useCallback(async()=>{
    if(!state||busyRef.current)return;
    let path:string|null|undefined;
    try{path=await api.suitePick('output');}catch(error){setNotice({text:problem(error),error:true});return;}
    if(!path)return;
    const saved=await run('Guardando SWF…',()=>api.suiteRpc<SuiteState&{path:string}>('export_swf',{path}));
    if(saved)setNotice({text:`SWF guardado: ${saved.path}`,error:false});
  },[state,run]);

  const cancel=useCallback(async()=>{
    setBusy('Cancelando…');
    try{
      await api.suiteCancel();
      const next=await api.suiteRpc<SuiteState>('state');
      await adopt(next);
      setNotice({text:'Operación cancelada. La sesión se recuperó y los borradores siguen en los editores.',error:false});
    }catch(error){setNotice({text:`Operación cancelada; abre el archivo de nuevo para continuar. ${problem(error)}`,error:true});}
    finally{busyRef.current=false;setBusy('');}
  },[adopt]);

  const draftStore:DraftStore=useMemo(()=>({
    get:<T,>(key:string)=>drafts.current.get(key) as T|undefined,
    set:<T,>(key:string,value:T|undefined)=>{if(value===undefined)drafts.current.delete(key);else drafts.current.set(key,value);setDraftVersion(version=>version+1);},
    count:drafts.current.size,
    has:(prefix:string)=>[...drafts.current.keys()].some(key=>key.startsWith(prefix)),
  // draftVersion is the change signal for consumers that render the count
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }),[draftVersion]);

  return {state,tree,catalog,busy,reading,notice,documentKey,recents,drafts:draftStore,
    notify,dismissNotice,run,track,apply,adopt,replaceDocument,loadTree,refreshCatalog,openPath,pickAndOpen,close,undo,redo,validate,saveProject,saveSwf,cancel,setCatalog};
}
export type Session=ReturnType<typeof useSession>;
