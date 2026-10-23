import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ArrowDownToLine,FolderOpen,Hammer,Redo2,Save,Settings2,Undo2} from 'lucide-react';
import * as api from '../api';
import {TreeNode} from '../types';
import {displayEngineName} from '../displayNames';
import DisplayPanel from './DisplayPanel';
import MenuBar,{MenuDef,MenuItem} from './MenuBar';
import SplitPane from './SplitPane';
import StatusBar from './StatusBar';
import TagTree from './TagTree';
import AbcToolsDialog from './dialogs/AbcToolsDialog';
import CompileDialog from './dialogs/CompileDialog';
import EnginesDialog from './dialogs/EnginesDialog';
import ExportDialog from './dialogs/ExportDialog';
import {AboutDialog,BenchmarksDialog,ConfirmDialog} from './dialogs/SmallDialogs';
import {branchIds,defaultExpansion,findNode,pathTo} from './treeModel';
import {useSession} from './useSession';
import {useSmokeVerification} from './smoke';
import {ThemeChoice,applyTheme,readStored,rememberTheme,storedTheme,writeStored} from './util';

type DialogId='compile'|'export'|'engines'|'benchmarks'|'about'|'abc';
interface Pending {title:string;message:string;confirmLabel:string;run:()=>void}
export const engineKey='flash-studio.decompile-engine';
const projectExtension=/\.(pyswfproj|flashproj)$/i;

/** The whole application: menu, toolbar, tag tree, display panel and status bar around one open document. */
export default function MainWindow(){
  const session=useSession();
  const {state,tree,busy,documentKey}=session;
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [expanded,setExpanded]=useState<Set<string>>(()=>new Set());
  const [filter,setFilter]=useState('');
  const [dialog,setDialog]=useState<DialogId|null>(null);
  const [pending,setPending]=useState<Pending|null>(null);
  const [theme,setTheme]=useState<ThemeChoice>(storedTheme);
  const [engine,setEngine]=useState(()=>readStored(engineKey)||'auto');
  const filterRef=useRef<HTMLInputElement>(null);
  const initialized=useRef(-1);

  const nodes=useMemo(()=>tree?.nodes??[],[tree]);
  const selected=useMemo(()=>selectedId?findNode(nodes,selectedId):null,[nodes,selectedId]);
  const chooseEngine=useCallback((next:string)=>{setEngine(next);writeStored(engineKey,next);},[]);
  /** Select a node chosen outside the tree (a table row, a link) and open its ancestors so the tree shows it. */
  const reveal=useCallback((node:TreeNode)=>{
    const path=pathTo(nodes,node.id);
    if(path&&path.length>1)setExpanded(previous=>{const next=new Set(previous);for(const ancestor of path.slice(0,-1))next.add(ancestor.id);return next;});
    setSelectedId(node.id);
  },[nodes]);

  useEffect(()=>applyTheme(theme),[theme]);
  useEffect(()=>{document.title=state?`${state.dirty?'● ':''}${state.filename} · Flash Universal Studio`:'Flash Universal Studio';},[state?.filename,state?.dirty]);
  useEffect(()=>{if(session.notice&&!session.notice.error){const timer=setTimeout(session.dismissNotice,9000);return()=>clearTimeout(timer);}},[session.notice,session.dismissNotice]);

  // A new document starts with scripts open and the header selected; a refreshed tree keeps the person's place.
  useEffect(()=>{
    if(!tree)return;
    if(initialized.current!==documentKey){
      initialized.current=documentKey;
      setExpanded(defaultExpansion(tree.nodes));setSelectedId('header');setFilter('');
    }else if(selectedId&&!findNode(tree.nodes,selectedId))setSelectedId(null);
  },[tree,documentKey,selectedId]);
  useEffect(()=>{if(!state&&!tree){setSelectedId(null);setFilter('');setExpanded(new Set());}},[state,tree]);

  /** Run `action` now, or after the person confirms that unsaved edits may be left behind. */
  const guarded=useCallback((title:string,action:()=>void)=>{
    if(state&&(state.dirty||session.drafts.count>0)){
      const drafts=session.drafts.count;
      setPending({title,confirmLabel:'Continuar',run:action,
        message:`El documento tiene ${state.dirty?'cambios aplicados que no se han guardado':''}${state.dirty&&drafts?' y ':''}${drafts?`${drafts} borrador${drafts===1?'':'es'} sin aplicar`:''}. La sesión se conserva en la carpeta de recuperación, pero no se escribirá ningún archivo. ¿Continuar?`});
    }else action();
  },[state,session.drafts.count]);

  const openSwf=useCallback(()=>guarded('Abrir otro archivo',()=>void session.pickAndOpen('swf')),[guarded,session]);
  const openProject=useCallback(()=>guarded('Abrir proyecto',()=>void session.pickAndOpen('project')),[guarded,session]);
  const closeDocument=useCallback(()=>guarded('Cerrar el documento',session.close),[guarded,session.close]);
  const dropped=useCallback((path:string)=>guarded('Abrir el archivo soltado',()=>void session.openPath(path,projectExtension.test(path))),[guarded,session]);

  const latest=useRef({openSwf,openProject,closeDocument,dropped,session});
  useEffect(()=>{latest.current={openSwf,openProject,closeDocument,dropped,session};});
  useEffect(()=>{
    let cancelled=false;
    void api.startup().then(info=>{if(!cancelled&&info.path)void latest.current.session.openPath(info.path,projectExtension.test(info.path));}).catch(error=>latest.current.session.notify(String(error),true));
    const stop=api.onFileDrop(path=>latest.current.dropped(path));
    return()=>{cancelled=true;stop();};
  },[]);

  useEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{
      if(!(event.ctrlKey||event.metaKey)||event.altKey)return;
      const key=event.key.toLowerCase();
      const target=event.target as HTMLElement|null;
      const editing=!!target?.closest?.('.cm-editor,input,textarea,select,[contenteditable="true"]');
      const now=latest.current;
      if(key==='o'){event.preventDefault();now.openSwf();}
      else if(key==='s'){event.preventDefault();if(event.shiftKey)void now.session.saveSwf();else void now.session.saveProject();}
      else if(key==='w'){event.preventDefault();now.closeDocument();}
      else if(key==='f'&&!event.shiftKey&&!target?.closest?.('.cm-editor')){event.preventDefault();filterRef.current?.focus();filterRef.current?.select();}
      else if((key==='z'||key==='y')&&!editing){
        event.preventDefault();
        if(key==='y'||event.shiftKey)void now.session.redo();else void now.session.undo();
      }
    };
    window.addEventListener('keydown',onKey);
    return()=>window.removeEventListener('keydown',onKey);
  },[]);

  useSmokeVerification({session,select:setSelectedId,nodes});

  const chooseTheme=(next:ThemeChoice)=>{setTheme(next);rememberTheme(next);};
  const hasDocument=!!state;
  const idle=!busy;
  const abcUnit=selected&&(selected.kind==='class'||selected.kind==='script')&&selected.language==='as3'?(selected.members?.[0]?.unit_id??selected.unit_id):undefined;
  const hasSavedSources=nodes.some(node=>node.id==='folder:sources');
  const themeItems:MenuItem[]=(['system','light','dark'] as const).map(choice=>({id:`theme-${choice}`,label:`Tema ${choice==='system'?'del sistema':choice==='light'?'claro':'oscuro'}`,checked:theme===choice,onSelect:()=>chooseTheme(choice)}));

  const menus:MenuDef[]=[
    {id:'file',label:'Archivo',items:[
      {id:'open',label:'Abrir SWF…',shortcut:'Ctrl+O',disabled:!idle,onSelect:openSwf},
      {id:'open-project',label:'Abrir proyecto…',disabled:!idle,onSelect:openProject},
      ...(session.recents.length?[{id:'sep-recent',separator:true} as MenuItem,...session.recents.map((item,index)=>({id:`recent-${index}`,label:`${index+1}  ${item.name}`,title:item.path,disabled:!idle,onSelect:()=>guarded('Abrir reciente',()=>void session.openPath(item.path,item.project))}))]:[]),
      {id:'sep-save',separator:true},
      {id:'save',label:'Guardar proyecto…',shortcut:'Ctrl+S',disabled:!hasDocument||!idle,title:'Guarda la sesión de trabajo en un archivo de proyecto nuevo',onSelect:()=>void session.saveProject()},
      {id:'save-as',label:'Guardar SWF como…',shortcut:'Ctrl+Mayús+S',disabled:!hasDocument||!idle,title:'Escribe la copia de trabajo en un SWF nuevo; el original no se toca',onSelect:()=>void session.saveSwf()},
      {id:'sep-export',separator:true},
      {id:'export',label:'Exportar…',disabled:!hasDocument||!idle,onSelect:()=>setDialog('export')},
      {id:'sep-close',separator:true},
      {id:'close',label:'Cerrar',shortcut:'Ctrl+W',disabled:!hasDocument,onSelect:closeDocument},
    ]},
    {id:'edit',label:'Edición',items:[
      {id:'undo',label:'Deshacer',shortcut:'Ctrl+Z',disabled:!state?.history.undo||!idle,onSelect:()=>void session.undo()},
      {id:'redo',label:'Rehacer',shortcut:'Ctrl+Y',disabled:!state?.history.redo||!idle,onSelect:()=>void session.redo()},
      {id:'sep-validate',separator:true},
      {id:'validate',label:'Validar SWF de trabajo',disabled:!hasDocument||!idle,onSelect:()=>void session.validate()},
    ]},
    {id:'view',label:'Ver',items:[
      ...themeItems,
      {id:'sep-tree',separator:true},
      {id:'expand',label:'Expandir todo el árbol',disabled:!nodes.length,onSelect:()=>setExpanded(new Set(branchIds(nodes)))},
      {id:'collapse',label:'Contraer todo el árbol',disabled:!nodes.length,onSelect:()=>setExpanded(new Set())},
      {id:'filter',label:'Filtrar el árbol',shortcut:'Ctrl+F',disabled:!hasDocument,onSelect:()=>filterRef.current?.focus()},
    ]},
    {id:'tools',label:'Herramientas',items:[
      {id:'compile',label:'Compilar…',disabled:!idle,onSelect:()=>setDialog('compile')},
      {id:'engines',label:'Motores…',onSelect:()=>setDialog('engines')},
      {id:'bench',label:'Benchmarks',onSelect:()=>setDialog('benchmarks')},
      {id:'sep-abc',separator:true},
      {id:'abc',label:'Ensamblado ABC (RABCDAsm)…',disabled:!abcUnit||!idle,title:'Requiere una clase o método ActionScript 3 seleccionado',onSelect:()=>setDialog('abc')},
    ]},
    {id:'help',label:'Ayuda',items:[{id:'about',label:'Acerca de Flash Universal Studio…',onSelect:()=>setDialog('about')}]},
  ];

  const engineLabel=engine==='auto'?'automático':displayEngineName(engine);
  return <div className="app" data-busy={busy?'true':undefined}>
    <MenuBar menus={menus}/>
    <div className="toolbar" role="toolbar" aria-label="Acciones frecuentes">
      <button type="button" className="tool-button" disabled={!idle} onClick={openSwf} title="Abrir SWF (Ctrl+O)"><FolderOpen size={15}/>Abrir</button>
      <button type="button" className="tool-button" disabled={!hasDocument||!idle} onClick={()=>void session.saveProject()} title="Guardar proyecto (Ctrl+S)"><Save size={15}/>Guardar</button>
      <button type="button" className="tool-button" disabled={!hasDocument||!idle} onClick={()=>setDialog('export')} title="Exportar"><ArrowDownToLine size={15}/>Exportar</button>
      <span className="toolbar-separator" aria-hidden="true"/>
      <button type="button" className="tool-button" disabled={!state?.history.undo||!idle} onClick={()=>void session.undo()} title="Deshacer (Ctrl+Z)"><Undo2 size={15}/>Deshacer</button>
      <button type="button" className="tool-button" disabled={!state?.history.redo||!idle} onClick={()=>void session.redo()} title="Rehacer (Ctrl+Y)"><Redo2 size={15}/>Rehacer</button>
      <span className="toolbar-separator" aria-hidden="true"/>
      <button type="button" className="tool-button" disabled={!idle} onClick={()=>setDialog('compile')} title="Compilar fuentes a SWF"><Hammer size={15}/>Compilar</button>
      <button type="button" className="tool-button" onClick={()=>setDialog('engines')} title="Motores y herramientas"><Settings2 size={15}/>Motores</button>
    </div>
    <SplitPane unit="px" storageKey="flash-studio.tree-width" initial={320} min={200} max={720} label="Ancho del árbol" className="workspace"
      first={<TagTree nodes={nodes} selectedId={selectedId} expanded={expanded} filter={filter} truncated={!!tree?.truncated} filterRef={filterRef}
        onSelect={(node:TreeNode)=>setSelectedId(node.id)} onExpandedChange={setExpanded} onFilterChange={setFilter}/>}
      second={<DisplayPanel node={selected} session={session} engine={engine} onEngineChange={chooseEngine} onSelect={reveal}
        startActions={{open:openSwf,openProject,compile:()=>setDialog('compile')}}/>}/>
    <StatusBar session={session} engine={engineLabel}/>
    {dialog==='compile'?<CompileDialog session={session} hasSavedSources={hasSavedSources||!!state?.source_pending} onClose={()=>setDialog(null)}/>:null}
    {dialog==='export'?<ExportDialog session={session} onClose={()=>setDialog(null)}/>:null}
    {dialog==='engines'?<EnginesDialog session={session} onClose={()=>setDialog(null)}/>:null}
    {dialog==='benchmarks'?<BenchmarksDialog onClose={()=>setDialog(null)}/>:null}
    {dialog==='about'?<AboutDialog onClose={()=>setDialog(null)}/>:null}
    {dialog==='abc'&&abcUnit&&selected?<AbcToolsDialog unitId={abcUnit} name={selected.label} session={session} onClose={()=>setDialog(null)}/>:null}
    {pending?<ConfirmDialog title={pending.title} confirmLabel={pending.confirmLabel} onConfirm={pending.run} onClose={()=>setPending(null)}><p>{pending.message}</p></ConfirmDialog>:null}
  </div>;
}
