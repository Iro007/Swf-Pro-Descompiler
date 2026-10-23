import {useEffect,useRef} from 'react';
import * as api from '../api';
import {TreeNode} from '../types';
import {Session} from './useSession';
import {problem} from './util';

const IMAGE_OR_SHAPE=new Set([2,22,32,83,6,20,21,35,36,90]);

function firstNode(nodes:TreeNode[],test:(node:TreeNode)=>boolean):TreeNode|undefined{
  for(const node of nodes){
    if(test(node))return node;
    const inner=node.children?firstNode(node.children,test):undefined;
    if(inner)return inner;
  }
  return undefined;
}
const sleep=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms));
async function until(check:()=>boolean,timeout=20000){
  const end=Date.now()+timeout;
  while(Date.now()<end){if(check())return true;await sleep(120);}
  return check();
}
const hasText=(selector:string)=>!!document.querySelector(`${selector} .cm-content`)?.textContent?.trim();

/**
 * Packaged-release verification (tools/verify-release.ps1 starts the application with a report file).
 * Opens a script and an image or shape through the real interface and reports what was visible.
 * Does nothing unless the host announced a verification run.
 */
export function useSmokeVerification({session,select,nodes}:{session:Session;select:(id:string)=>void;nodes:TreeNode[]}){
  const wanted=useRef<boolean|null>(null);
  const started=useRef(false);
  useEffect(()=>{void api.startup().then(info=>{wanted.current=info.smoke;}).catch(()=>{wanted.current=false;});},[]);
  useEffect(()=>{
    if(started.current||!session.state||!nodes.length||session.busy)return;
    let cancelled=false;
    const timer=setTimeout(()=>{
      if(!wanted.current||started.current||cancelled)return;
      started.current=true;
      void(async()=>{
        const evidence:Record<string,unknown>={native:true,filename:session.state?.filename,read:true};
        try{
          const script=firstNode(nodes,node=>node.kind==='class'||node.kind==='script');
          if(script){
            select(script.id);
            await until(()=>hasText('[data-testid="script-source"]')&&hasText('[data-testid="script-pcode"]'));
            evidence.script_id=script.id;
            evidence.source_visible=hasText('[data-testid="script-source"]');
            evidence.pcode_visible=hasText('[data-testid="script-pcode"]');
          }
          const asset=firstNode(nodes,node=>node.kind==='tag'&&!node.tag_path&&IMAGE_OR_SHAPE.has(node.tag_type??-1));
          if(asset){
            select(asset.id);
            await until(()=>{const image=document.querySelector<HTMLImageElement>('.preview-surface img');return !!image&&image.complete&&image.naturalWidth>0;});
            evidence.resource_id=asset.id;
            evidence.resource_visible=!!document.querySelector<HTMLImageElement>('.preview-surface img')?.naturalWidth;
            document.querySelector<HTMLButtonElement>('#tab-hex')?.click();
            await until(()=>!!document.querySelector('[data-testid="hex-dump"]')?.textContent?.includes('00000000'));
            evidence.hex_visible=!!document.querySelector('[data-testid="hex-dump"]')?.textContent?.includes('00000000');
          }
          evidence.success=(!script||(evidence.source_visible===true&&evidence.pcode_visible===true))&&(!asset||(evidence.resource_visible===true&&evidence.hex_visible===true));
        }catch(error){evidence.success=false;evidence.error=problem(error);}
        await api.reportVerification(evidence).catch(()=>undefined);
      })();
    },600);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[session.state,session.busy,nodes,select]);
}
