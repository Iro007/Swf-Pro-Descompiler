/**
 * `ui/src/api.ts` for Electron: the only module of the interface that talks to its host, written
 * against `window.flash` (desktop/preload.js). It exports exactly what the Tauri version exports, so
 * putting it in place of that file is all the interface needs to run inside the desktop host
 * (`npm run try-ui` builds the interface with it and opens the window; a test keeps the two
 * export lists equal).
 *
 * Each function maps one to one onto the preload (engines/PROTOCOL.md, section 8):
 *   native -> `window.flash` exists          suiteRpc -> call(op, args), unwrapped
 *   suitePick -> openDialog(kind)            suiteCancel -> cancel()
 *   startup -> startup()                     reportVerification -> reportVerification(evidence)
 *   onFileDrop -> onFileDrop(callback)
 */
import {SuitePickKind} from './types';

interface Reply {ok:true;result:unknown}
interface Failure {ok:false;error:{message:string;kind:string}}
interface Flash {
  call(op:string,args?:Record<string,unknown>):Promise<Reply|Failure>;
  cancel():Promise<unknown>;
  openDialog(kind:string):Promise<string|null>;
  startup():Promise<StartupInfo>;
  reportVerification(evidence:unknown):Promise<unknown>;
  onFileDrop(handler:(path:string)=>void):()=>void;
}
const host=():Flash|undefined=>(typeof window!=='undefined'?(window as unknown as {flash?:Flash}).flash:undefined);

/** True inside the desktop application; false in a plain browser tab. */
export const native=host()!==undefined;

/** Run one operation of an engine (engines/PROTOCOL.md) and return its result; a failure throws its message. */
export const suiteRpc=async<T,>(operation:string,args:Record<string,unknown>={}):Promise<T>=>{
  const reply=await host()!.call(operation,args);
  if(!reply.ok)throw new Error(reply.error.message);
  return reply.result as T;
};
/** Native file/folder dialogs; resolves to null when the person cancels. */
export const suitePick=(kind:SuitePickKind)=>host()!.openDialog(kind);
/** Stop the running operation; the next one recovers the session from the document. */
export const suiteCancel=async():Promise<void>=>{await host()!.cancel();};

export interface StartupInfo {path:string|null;smoke:boolean}
/** File passed on the command line (`--open`) and whether a verification report was requested. */
export const startup=():Promise<StartupInfo>=>native?host()!.startup():Promise.resolve({path:null,smoke:false});
/** Evidence of a verification run (`--smoke-report`); ignored outside the desktop application. */
export const reportVerification=async(verification:unknown):Promise<void>=>{if(native)await host()!.reportVerification(verification);};

/** Files dropped on the window. Returns the function that stops listening. */
export const onFileDrop=(handler:(path:string)=>void):(()=>void)=>native?host()!.onFileDrop(handler):()=>undefined;
