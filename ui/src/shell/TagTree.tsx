import {KeyboardEvent,useEffect,useMemo,useRef,useState} from 'react';
import {AlertTriangle,ChevronDown,ChevronRight,ChevronsDownUp,ChevronsUpDown,Search,X} from 'lucide-react';
import {TreeNode} from '../types';
import {Row,branchIds,flatten} from './treeModel';
import {groupOf,iconFor} from './treeIcons';
import {bytesText} from './util';

const ROW_HEIGHT=24;
const OVERSCAN=10;

interface Props {
  nodes:TreeNode[];selectedId:string|null;expanded:Set<string>;filter:string;truncated:boolean;
  onSelect:(node:TreeNode)=>void;onExpandedChange:(next:Set<string>)=>void;onFilterChange:(value:string)=>void;
  /** Asks the owner to move focus to the filter box (Ctrl+F). */
  filterRef:React.RefObject<HTMLInputElement|null>;
}

/** JPEXS-style tree: virtualised rows, folders per kind, quick filter and the WAI-ARIA tree keyboard model. */
export default function TagTree({nodes,selectedId,expanded,filter,truncated,onSelect,onExpandedChange,onFilterChange,filterRef}:Props){
  const scroller=useRef<HTMLDivElement>(null);
  const [scrollTop,setScrollTop]=useState(0);
  const [height,setHeight]=useState(480);
  const [typed,setTyped]=useState(filter);
  const rows=useMemo(()=>flatten(nodes,expanded,filter),[nodes,expanded,filter]);
  const selectedRow=selectedId===null?-1:rows.findIndex(row=>row.node.id===selectedId);

  useEffect(()=>{setTyped(filter);},[filter]);
  useEffect(()=>{const timer=setTimeout(()=>{if(typed!==filter)onFilterChange(typed);},140);return()=>clearTimeout(timer);},[typed,filter,onFilterChange]);
  useEffect(()=>{
    const element=scroller.current;if(!element)return;
    const measure=()=>setHeight(element.clientHeight||480);
    measure();
    if(typeof ResizeObserver==='undefined')return;
    const observer=new ResizeObserver(measure);observer.observe(element);return()=>observer.disconnect();
  },[]);

  const reveal=(index:number)=>{
    const element=scroller.current;if(!element||index<0)return;
    const top=index*ROW_HEIGHT;
    if(top<element.scrollTop)element.scrollTop=top;
    else if(top+ROW_HEIGHT>element.scrollTop+element.clientHeight)element.scrollTop=top+ROW_HEIGHT-element.clientHeight;
  };
  useEffect(()=>{reveal(selectedRow);},[selectedRow,selectedId]);

  const toggle=(id:string,open?:boolean)=>{
    const next=new Set(expanded);
    const shouldOpen=open??!next.has(id);
    if(shouldOpen)next.add(id);else next.delete(id);
    onExpandedChange(next);
  };
  const move=(index:number)=>{const row=rows[Math.max(0,Math.min(rows.length-1,index))];if(row)onSelect(row.node);};
  const onKeyDown=(event:KeyboardEvent<HTMLDivElement>)=>{
    if(!rows.length)return;
    const row:Row|undefined=selectedRow>=0?rows[selectedRow]:undefined;
    switch(event.key){
      case 'ArrowDown':event.preventDefault();move(selectedRow<0?0:selectedRow+1);break;
      case 'ArrowUp':event.preventDefault();move(selectedRow<0?0:selectedRow-1);break;
      case 'Home':event.preventDefault();move(0);break;
      case 'End':event.preventDefault();move(rows.length-1);break;
      case 'PageDown':event.preventDefault();move(selectedRow+Math.max(1,Math.floor(height/ROW_HEIGHT)-1));break;
      case 'PageUp':event.preventDefault();move(selectedRow-Math.max(1,Math.floor(height/ROW_HEIGHT)-1));break;
      case 'ArrowRight':
        event.preventDefault();
        if(row?.expandable&&!row.expanded)toggle(row.node.id,true);
        else if(row?.expanded)move(selectedRow+1);
        break;
      case 'ArrowLeft':
        event.preventDefault();
        if(row?.expanded)toggle(row.node.id,false);
        else if(row?.parentId){const parent=rows.findIndex(candidate=>candidate.node.id===row.parentId);if(parent>=0)move(parent);}
        break;
      case 'Enter':case ' ':
        event.preventDefault();
        if(row?.expandable)toggle(row.node.id);else if(row)onSelect(row.node);
        break;
      case '*':event.preventDefault();onExpandedChange(new Set([...expanded,...branchIds(nodes)]));break;
      default:
    }
  };

  const first=Math.max(0,Math.floor(scrollTop/ROW_HEIGHT)-OVERSCAN);
  const last=Math.min(rows.length,Math.ceil((scrollTop+height)/ROW_HEIGHT)+OVERSCAN);
  const everything=useMemo(()=>branchIds(nodes),[nodes]);

  return <section className="tree" aria-label="Estructura del SWF">
    <div className="tree-tools">
      <label className="tree-filter"><Search size={14} aria-hidden="true"/>
        <input ref={filterRef} value={typed} onChange={event=>setTyped(event.target.value)} placeholder="Filtrar (Ctrl+F)" aria-label="Filtrar el árbol"
          onKeyDown={event=>{if(event.key==='Escape'){setTyped('');onFilterChange('');scroller.current?.focus();}else if(event.key==='ArrowDown'){event.preventDefault();scroller.current?.focus();if(rows.length&&selectedRow<0)move(0);}}}/>
        {typed?<button type="button" className="icon-button small" aria-label="Limpiar filtro" onClick={()=>{setTyped('');onFilterChange('');}}><X size={13}/></button>:null}
      </label>
      <button type="button" className="icon-button" title="Expandir todo" aria-label="Expandir todo" disabled={!everything.length} onClick={()=>onExpandedChange(new Set(everything))}><ChevronsUpDown size={15}/></button>
      <button type="button" className="icon-button" title="Contraer todo" aria-label="Contraer todo" disabled={!expanded.size} onClick={()=>onExpandedChange(new Set())}><ChevronsDownUp size={15}/></button>
    </div>
    {truncated?<p className="tree-note" role="status"><AlertTriangle size={13} aria-hidden="true"/>El árbol se recortó por su tamaño; usa el filtro o los paneles para el resto.</p>:null}
    <div ref={scroller} className="tree-scroll" role="tree" aria-label="Elementos del SWF" tabIndex={0} onKeyDown={onKeyDown}
      onScroll={event=>setScrollTop(event.currentTarget.scrollTop)} aria-activedescendant={selectedRow>=0?`tree-row-${rows[selectedRow].node.id}`:undefined}>
      <div className="tree-space" style={{height:rows.length*ROW_HEIGHT}}>
        {rows.slice(first,last).map(row=>{
          const {node}=row;const selected=node.id===selectedId;
          const Icon=iconFor(node,row.expanded);
          const count=node.kind==='folder'||node.kind==='package'?node.child_count:undefined;
          return <div key={node.id} id={`tree-row-${node.id}`} role="treeitem" aria-level={row.depth+1} aria-selected={selected} aria-expanded={row.expandable?row.expanded:undefined}
            className={`tree-row group-${groupOf(node)} ${selected?'selected':''} ${node.error?'has-error':''}`} style={{top:row.index*ROW_HEIGHT,height:ROW_HEIGHT,paddingLeft:6+row.depth*16}}
            title={[node.label,node.detail,node.error].filter(Boolean).join(' · ')}
            onClick={()=>onSelect(node)} onDoubleClick={()=>{if(row.expandable)toggle(node.id);}}>
            <span className="tree-chevron" onClick={event=>{if(row.expandable){event.stopPropagation();toggle(node.id);}}} aria-hidden="true">
              {row.expandable?(row.expanded?<ChevronDown size={14}/>:<ChevronRight size={14}/>):null}
            </span>
            <Icon size={15} className="tree-icon" aria-hidden="true"/>
            <span className="tree-label">{node.label}</span>
            {node.detail&&(node.kind!=='class'||filter)?<span className="tree-detail">{node.detail}</span>:null}
            {node.error?<AlertTriangle size={12} className="tree-warning" aria-label="Con diagnóstico"/>:null}
            {count!==undefined?<span className="tree-count">{count}</span>:node.kind==='tag'&&node.bytes!==undefined&&node.bytes>=1024?<span className="tree-count">{bytesText(node.bytes)}</span>:null}
          </div>;
        })}
      </div>
      {!rows.length?<p className="tree-empty">{filter?'Ningún elemento coincide con el filtro.':'Abre un SWF para ver su contenido.'}</p>:null}
    </div>
  </section>;
}
