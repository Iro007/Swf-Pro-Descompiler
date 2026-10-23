import {TreeNode} from '../types';
import {groupOf,iconFor} from './treeIcons';
import {bytesText} from './util';

/** Contents of a folder or package: a table the person can navigate by clicking a row. */
export default function FolderPanel({node,onSelect}:{node:TreeNode;onSelect:(node:TreeNode)=>void}){
  const children=node.children??[];
  const shown=children.slice(0,500);
  return <div className="folder-panel" data-testid="folder-panel">
    <p className="panel-muted">{node.child_count??children.length} elemento{(node.child_count??children.length)===1?'':'s'}{node.detail?` · ${node.detail}`:''}. Selecciona uno para ver su contenido.</p>
    <table className="data-table">
      <thead><tr><th scope="col">Elemento</th><th scope="col">Detalle</th><th scope="col" className="num">Tamaño</th></tr></thead>
      <tbody>{shown.map(child=>{
        const Icon=iconFor(child,false);
        return <tr key={child.id} tabIndex={0} onClick={()=>onSelect(child)} onKeyDown={event=>{if(event.key==='Enter')onSelect(child);}}>
          <td><span className={`row-label group-${groupOf(child)}`}><Icon size={14} aria-hidden="true"/>{child.label}</span></td>
          <td className="dim">{child.detail??''}</td>
          <td className="num dim">{child.bytes!==undefined?bytesText(child.bytes):child.child_count!==undefined?`${child.child_count}`:''}</td>
        </tr>;})}</tbody>
    </table>
    {children.length>shown.length?<p className="panel-muted">Se muestran los primeros {shown.length} de {children.length}; usa el filtro del árbol para encontrar el resto.</p>:null}
  </div>;
}
