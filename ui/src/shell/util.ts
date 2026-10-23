import {useEffect,useRef} from 'react';

/** Human readable size in binary units. */
export const bytesText=(n:number)=>n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KiB`:`${(n/1048576).toFixed(1)} MiB`;
export const shortRevision=(revision:string|undefined|null)=>String(revision??'').slice(0,10);
export const percentText=(value:number|null|undefined)=>typeof value==='number'&&Number.isFinite(value)?`${Math.round(value*100)}%`:'—';

/** Readable text for whatever a failed host call throws (Error, string or {message}). */
export function problem(value:unknown):string{
  if(value instanceof Error)return value.message;
  if(typeof value==='string')return value;
  if(value&&typeof value==='object'&&'message'in value)return String((value as {message:unknown}).message);
  try{return JSON.stringify(value,null,2)??String(value);}catch{return String(value);}
}

/** localStorage may be missing or throw (private windows, blocked storage, thumbnail capture). */
export function readStored(key:string):string|null{try{return localStorage.getItem(key);}catch{return null;}}
export function writeStored(key:string,value:string):void{try{localStorage.setItem(key,value);}catch{/* the interface works without storage */}}
export function readStoredNumber(key:string,fallback:number):number{const value=Number(readStored(key));return Number.isFinite(value)&&value>0?value:fallback;}

export type ThemeChoice='system'|'light'|'dark';
const themeKey='flash-studio.theme';
export function storedTheme():ThemeChoice{const value=readStored(themeKey);return value==='light'||value==='dark'?value:'system';}
/** Resolve the choice against the system preference and mark the document (CSS only knows light and dark). */
export function applyTheme(choice:ThemeChoice):()=>void{
  const query=window.matchMedia?.('(prefers-color-scheme: light)');
  const resolve=()=>{document.documentElement.dataset.theme=choice==='system'?(query?.matches?'light':'dark'):choice;};
  resolve();
  if(choice==='system')query?.addEventListener?.('change',resolve);
  return()=>query?.removeEventListener?.('change',resolve);
}
export function rememberTheme(choice:ThemeChoice):void{writeStored(themeKey,choice);}

/** Latest value in a ref, for callbacks registered once. */
export function useLatest<T>(value:T){const ref=useRef(value);useEffect(()=>{ref.current=value;});return ref;}

/** Base64 (as sent by the suite) to bytes. */
export function base64Bytes(data:string):Uint8Array{const binary=atob(data);const bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);return bytes;}
