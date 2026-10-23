import {TreeNode} from '../types';

export interface Row {node:TreeNode;depth:number;parentId:string|null;expandable:boolean;expanded:boolean;index:number}

const haystacks=new WeakMap<TreeNode,string>();
/** Lower-cased searchable text of a node: label, detail, tag name, character id and class members. */
function haystack(node:TreeNode):string{
  let text=haystacks.get(node);
  if(text===undefined){
    text=[node.label,node.detail,node.tag_name,node.character_id,node.unit_id,node.document_id,node.members?.map(member=>member.name).join(' ')].filter(part=>part!==undefined&&part!=='').join(' ').toLowerCase();
    haystacks.set(node,text);
  }
  return text;
}

export function findNode(nodes:TreeNode[],id:string):TreeNode|null{
  for(const node of nodes){
    if(node.id===id)return node;
    const found=node.children?findNode(node.children,id):null;
    if(found)return found;
  }
  return null;
}

/** The chain of nodes from a root down to `id`, or null when it is not in the tree. */
export function pathTo(nodes:TreeNode[],id:string):TreeNode[]|null{
  for(const node of nodes){
    if(node.id===id)return [node];
    const below=node.children?pathTo(node.children,id):null;
    if(below)return [node,...below];
  }
  return null;
}

export function countNodes(nodes:TreeNode[]):number{let total=0;for(const node of nodes)total+=1+(node.children?countNodes(node.children):0);return total;}

/** Ids of nodes that can be expanded, down to `depth` levels (all of them when omitted). */
export function branchIds(nodes:TreeNode[],depth=Infinity):string[]{
  const ids:string[]=[];
  const visit=(list:TreeNode[],level:number)=>{for(const node of list)if(node.children?.length&&level<depth){ids.push(node.id);visit(node.children,level+1);}};
  visit(nodes,0);return ids;
}

/** Scripts open by default, following chains of single children (default package, a lone package…). */
export function defaultExpansion(nodes:TreeNode[]):Set<string>{
  const open=new Set<string>();
  const scripts=nodes.find(node=>node.id==='folder:scripts');
  let current=scripts;
  while(current?.children?.length){
    open.add(current.id);
    if(current.children.length!==1||!current.children[0].children)break;
    current=current.children[0];
  }
  return open;
}

/**
 * Visible rows in display order. With a filter only matches and their ancestors remain, and every
 * ancestor of a match is open. A matching branch that the person opened shows all of its content.
 */
export function flatten(nodes:TreeNode[],expanded:ReadonlySet<string>,filter:string):Row[]{
  const query=filter.trim().toLowerCase();
  const rows:Row[]=[];
  const visit=(list:TreeNode[],depth:number,parentId:string|null,showAll:boolean):boolean=>{
    let any=false;
    for(const node of list){
      const hasChildren=!!node.children?.length;
      const own=showAll||haystack(node).includes(query);
      const slot=rows.length;
      rows.push({node,depth,parentId,expandable:hasChildren,expanded:false,index:slot});
      let open=false;
      if(hasChildren){
        if(showAll)open=expanded.has(node.id)&&(visit(node.children!,depth+1,node.id,true),true);
        else{
          const everything=own&&expanded.has(node.id);
          open=visit(node.children!,depth+1,node.id,everything)||everything;
        }
      }
      if(!own&&!open){rows.length=slot;continue;}
      rows[slot].expanded=open;
      any=true;
    }
    return any;
  };
  visit(nodes,0,null,!query);
  rows.forEach((row,index)=>{row.index=index;});
  return rows;
}
