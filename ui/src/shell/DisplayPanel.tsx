import {ReactNode,useState} from 'react';
import {TreeNode} from '../types';
import FolderPanel from './FolderPanel';
import HeaderPanel from './HeaderPanel';
import HexPanel,{HexTarget} from './HexPanel';
import PreviewPanel,{PreviewSource} from './PreviewPanel';
import ScriptPanel from './ScriptPanel';
import SourceFilePanel from './SourceFilePanel';
import StartPanel from './StartPanel';
import TagInfoPanel from './TagInfoPanel';
import {groupOf,iconFor} from './treeIcons';
import {Session} from './useSession';

interface Tab {id:string;label:string}
/** Characters the suite can draw without running the movie. */
const previewable=new Set([2,22,32,83,46,84,39,6,20,21,35,36,90,11,33,48,75]);

function tabsFor(node:TreeNode):Tab[]{
  switch(node.kind){
    case 'header':return [{id:'info',label:'Cabecera'},{id:'preview',label:'Película'},{id:'hex',label:'Hex del archivo'}];
    case 'frame':return [{id:'preview',label:'Vista previa'},{id:'contents',label:'Etiquetas'}];
    case 'tag':return [...(node.tag_type!==undefined&&previewable.has(node.tag_type)&&!node.tag_path?[{id:'preview',label:'Vista previa'}]:[]),{id:'info',label:'Propiedades'},{id:'hex',label:'Hex'}];
    default:return [];
  }
}

function hexTarget(node:TreeNode):HexTarget{
  if(node.kind==='header')return {scope:'file'};
  return node.tag_path&&node.tag_path.length>1?{tagPath:node.tag_path}:{tagIndex:node.tag_index};
}

function previewSource(node:TreeNode):PreviewSource{
  if(node.kind==='header')return {type:'movie'};
  if(node.kind==='frame')return {type:'frame',frame:node.frame??1,spriteIndex:node.tag_index};
  return {type:'character',tagIndex:node.tag_index!,name:node.label,tagType:node.tag_type??0};
}

/** Right-hand side: picks the card for the selected node, as the JPEXS display panel does. */
export default function DisplayPanel({node,session,engine,onEngineChange,onSelect,startActions}:{node:TreeNode|null;session:Session;engine:string;onEngineChange:(engine:string)=>void;onSelect:(node:TreeNode)=>void;startActions:{open:()=>void;openProject:()=>void;compile:()=>void}}){
  const [view,setView]=useState({context:'',tab:''});
  if(!session.state)return <StartPanel busy={!!session.busy} onOpen={startActions.open} onOpenProject={startActions.openProject} onCompile={startActions.compile}/>;
  if(!node)return <div className="display-empty"><p className="panel-muted">Selecciona un elemento del árbol para ver su contenido.</p></div>;

  const tabs=tabsFor(node);
  // The chosen tab sticks while browsing similar items (several images, one after another) and resets for another kind.
  const context=`${node.kind}:${groupOf(node)}`;
  const tab=view.context===context?view.tab:'';
  const active=tabs.some(item=>item.id===tab)?tab:tabs[0]?.id??'';
  const Icon=iconFor(node,false);
  const title=node.kind==='class'?`${node.detail?node.detail+'.':''}${node.label}`:node.label;
  let body:ReactNode;
  if(node.kind==='class'||node.kind==='script')body=<ScriptPanel key={node.id} node={node} session={session} engine={engine} onEngineChange={onEngineChange}/>;
  else if(node.kind==='source')body=<SourceFilePanel key={node.id} node={node} session={session}/>;
  else if(node.kind==='folder'||node.kind==='package')body=<FolderPanel node={node} onSelect={onSelect}/>;
  else if(active==='info'&&node.kind==='header')body=<HeaderPanel session={session}/>;
  else if(active==='info')body=<TagInfoPanel node={node} session={session}/>;
  else if(active==='preview')body=<PreviewPanel source={previewSource(node)} session={session}/>;
  else if(active==='hex')body=<HexPanel target={hexTarget(node)} session={session}/>;
  else if(active==='contents')body=<FolderPanel node={node} onSelect={onSelect}/>;
  else body=<TagInfoPanel node={node} session={session}/>;

  return <section className="display" aria-label={`Contenido de ${title}`}>
    <header className="display-heading">
      <Icon size={16} className={`group-${groupOf(node)}`} aria-hidden="true"/>
      <h1>{title}</h1>
      {node.kind==='tag'&&node.detail&&!node.detail.startsWith('#')?<span className="dim">{node.detail}</span>:null}
      {node.kind==='tag'&&node.tag_name?<span className="badge">{node.tag_name}</span>:null}
    </header>
    {tabs.length?<div className="tabs" role="tablist" aria-label="Vistas del elemento">
      {tabs.map(item=><button type="button" key={item.id} role="tab" id={`tab-${item.id}`} aria-selected={active===item.id} aria-controls="display-body" onClick={()=>setView({context,tab:item.id})}>{item.label}</button>)}
    </div>:null}
    <div className="display-body" id="display-body" role={tabs.length?'tabpanel':undefined} aria-labelledby={tabs.length?`tab-${active}`:undefined}>{body}</div>
  </section>;
}
