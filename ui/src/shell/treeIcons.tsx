import {ALargeSmall,Binary,Box,File,FileCode2,Film,Folder,FolderOpen,Frame,Image as ImageIcon,Info,Layers,MousePointerClick,Move,Package,Puzzle,Shapes,Tag,Type,Volume2,Wand2} from 'lucide-react';
import type {LucideIcon} from 'lucide-react';
import {TreeNode} from '../types';

/** Visual group of a node: drives both the icon and its colour token. */
export type IconGroup='folder'|'header'|'shape'|'morph'|'sprite'|'text'|'image'|'movie'|'sound'|'button'|'font'|'binary'|'frame'|'code'|'package'|'class'|'control'|'other';

const folderGroups:Record<string,IconGroup>={shapes:'shape',morphshapes:'morph',sprites:'sprite',texts:'text',images:'image',movies:'movie',sounds:'sound',buttons:'button',fonts:'font',binaryData:'binary',frames:'frame',others:'other',scripts:'code',sources:'code'};
const typeGroups:Record<number,IconGroup>={
  2:'shape',22:'shape',32:'shape',83:'shape',46:'morph',84:'morph',39:'sprite',11:'text',33:'text',37:'text',
  6:'image',20:'image',21:'image',35:'image',36:'image',90:'image',38:'movie',60:'movie',14:'sound',18:'sound',45:'sound',
  7:'button',34:'button',10:'font',48:'font',75:'font',91:'font',87:'binary',
  4:'control',5:'control',26:'control',28:'control',70:'control',94:'control',43:'control',15:'control',89:'control',
};
const icons:Record<IconGroup,LucideIcon>={
  folder:Folder,header:Info,shape:Shapes,morph:Wand2,sprite:Layers,text:Type,image:ImageIcon,movie:Film,sound:Volume2,button:MousePointerClick,
  font:ALargeSmall,binary:Binary,frame:Frame,code:FileCode2,package:Package,class:Box,control:Move,other:File,
};

export function groupOf(node:TreeNode):IconGroup{
  switch(node.kind){
    case 'folder':return folderGroups[node.folder??'']??'folder';
    case 'header':return 'header';
    case 'package':return 'package';
    case 'class':return 'class';
    case 'script':case 'source':return 'code';
    case 'frame':return 'frame';
    default:return typeGroups[node.tag_type??-1]??'other';
  }
}

export function iconFor(node:TreeNode,open:boolean):LucideIcon{
  if(node.kind==='folder'&&!folderGroups[node.folder??''])return open?FolderOpen:Folder;
  if(node.kind==='package')return open?FolderOpen:Package;
  if(node.kind==='tag'&&node.tag_type===43)return Tag;
  if(node.kind==='tag'&&node.tag_type===undefined)return Puzzle;
  return icons[groupOf(node)];
}
