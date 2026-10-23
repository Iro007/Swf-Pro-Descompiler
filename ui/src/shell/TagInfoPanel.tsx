import {TreeNode} from '../types';
import {Session} from './useSession';
import ResourceEditor,{editableTypes} from './ResourceEditor';
import {bytesText} from './util';

/** Facts about one tag, plus the structured editor when the tag type supports one. */
export default function TagInfoPanel({node,session}:{node:TreeNode;session:Session}){
  const path=node.tag_path??(node.tag_index!==undefined?[node.tag_index]:[]);
  const editable=node.kind==='tag'&&node.tag_index!==undefined&&!node.tag_path&&node.tag_type!==undefined&&editableTypes.has(node.tag_type);
  return <div className="info-panel" data-testid="tag-info">
    <dl className="facts">
      <dt>Etiqueta</dt><dd>{node.tag_name??node.label}</dd>
      {node.tag_type!==undefined?<><dt>Código</dt><dd>{node.tag_type}</dd></>:null}
      {path.length?<><dt>Posición</dt><dd>#{path.join(' / ')}{path.length>1?' (dentro de un sprite)':''}</dd></>:null}
      {node.character_id!==undefined?<><dt>Id de carácter</dt><dd>{node.character_id}</dd></>:null}
      {node.detail&&node.kind==='tag'&&!node.detail.startsWith('#')?<><dt>Nombre exportado</dt><dd>{node.detail}</dd></>:null}
      {node.bytes!==undefined?<><dt>Contenido</dt><dd>{node.bytes.toLocaleString()} bytes <span className="dim">({bytesText(node.bytes)})</span></dd></>:null}
      {node.child_count?<><dt>Contiene</dt><dd>{node.child_count} elemento{node.child_count===1?'':'s'}</dd></>:null}
    </dl>
    {node.error?<p className="panel-error" role="alert">{node.error}</p>:null}
    {editable?<ResourceEditor tagIndex={node.tag_index!} session={session}/>:null}
  </div>;
}
