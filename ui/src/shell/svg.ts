import {useEffect,useState} from 'react';
import {base64Bytes} from './util';

/**
 * The suite draws SVG itself, but the document still comes from an untrusted file:
 * keep it inert (no scripts, no foreign content, no external references) before showing it.
 * Fragment references and small embedded PNG/JPEG bitmaps (bitmap fills) are the only links kept.
 */
export function sanitizeSvg(svg:string):string|null{
  const parsed=new DOMParser().parseFromString(svg,'image/svg+xml');
  if(parsed.querySelector('parsererror')||parsed.documentElement.localName!=='svg')return null;
  parsed.querySelectorAll('script,foreignObject,iframe,object,embed,style,animate,animateTransform,animateMotion,set').forEach(element=>element.remove());
  parsed.querySelectorAll('*').forEach(element=>{
    for(const attribute of Array.from(element.attributes)){
      const name=attribute.name.toLowerCase();const value=attribute.value.trim();
      const link=name==='href'||name==='xlink:href';
      const safeLink=value.startsWith('#')||/^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]*$/.test(value);
      const safeUrl=!/url\(/i.test(value)||/^url\(\s*['"]?#[\w.-]+['"]?\s*\)$/i.test(value);
      if(name.startsWith('on')||name==='style'||(link&&!safeLink)||!safeUrl)element.removeAttribute(attribute.name);
    }
  });
  return new XMLSerializer().serializeToString(parsed.documentElement);
}

export interface PreviewPayload {mime:string;encoding:'base64'|'text';data:string}
/** Blob URL for a preview (revoked when it changes); null when the payload is unusable. */
export function usePreviewUrl(payload:PreviewPayload|null|undefined):string|null{
  const [url,setUrl]=useState<string|null>(null);
  useEffect(()=>{
    if(!payload){setUrl(null);return;}
    let blob:Blob|null=null;
    try{
      if(payload.mime==='image/svg+xml'){const clean=sanitizeSvg(payload.data);blob=clean?new Blob([clean],{type:'image/svg+xml'}):null;}
      else blob=new Blob([base64Bytes(payload.data) as BlobPart],{type:payload.mime});
    }catch{blob=null;}
    if(!blob){setUrl(null);return;}
    const next=URL.createObjectURL(blob);setUrl(next);
    return()=>URL.revokeObjectURL(next);
  },[payload?.mime,payload?.data,payload?.encoding]);
  return url;
}
