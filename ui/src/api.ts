/**
 * The only module that talks to the desktop host (Tauri today, Electron next).
 * Everything else in the interface calls these small functions, so swapping the
 * host means rewriting this file and nothing else.
 */
import {invoke, isTauri} from '@tauri-apps/api/core';
import {getCurrentWebview} from '@tauri-apps/api/webview';
import {SuitePickKind} from './types';

/** True inside the desktop application; false in a plain browser tab. */
export const native=isTauri();

/** Run one operation of the Python suite (JSONL protocol v1). */
export const suiteRpc=<T,>(operation:string,args:Record<string,unknown>={})=>invoke<T>('suite_rpc',{operation,args});
/** Native file/folder dialogs; resolves to null when the person cancels. */
export const suitePick=(kind:SuitePickKind)=>invoke<string|null>('suite_pick',{kind});
/** Stop the running suite operation; the next one recovers the session from disk. */
export const suiteCancel=()=>invoke<void>('suite_cancel');

export interface StartupInfo {path:string|null;smoke:boolean}
/** File passed on the command line (`--open`) and whether a verification report was requested. */
export const startup=():Promise<StartupInfo>=>native?invoke<StartupInfo>('startup'):Promise.resolve({path:null,smoke:false});
/** Evidence for tools/verify-release.ps1; ignored outside the desktop application. */
export const reportVerification=(verification:unknown):Promise<void>=>native?invoke<void>('ui_verified',{evidence:{verification}}):Promise.resolve();

/** Files dropped on the window. Returns the function that stops listening. */
export const onFileDrop=(handler:(path:string)=>void):(()=>void)=>{
  if(!native)return()=>undefined;
  const pending=getCurrentWebview().onDragDropEvent(event=>{if(event.payload.type==='drop'&&event.payload.paths[0])handler(event.payload.paths[0]);});
  return()=>{void pending.then(unlisten=>unlisten()).catch(()=>undefined);};
};
