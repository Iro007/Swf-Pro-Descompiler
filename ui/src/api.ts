/**
 * The only module of the interface that talks to its host: the Electron application. It is written
 * against `window.flash` (desktop/preload.js), so everything else in the interface calls these small
 * functions and knows nothing of Electron; a plain browser tab has no `window.flash` and `native` is false.
 *
 * Each function maps one to one onto the preload (engines/PROTOCOL.md, section 8):
 *   native -> `window.flash` exists          suiteRpc -> call(op, args), unwrapped
 *   suitePick -> openDialog(kind)            suiteCancel -> cancel()
 *   startup -> startup()                     reportVerification -> reportVerification(evidence)
 *   onFileDrop -> onFileDrop(callback)
 * (desktop/test/docs.test.js keeps this list and the preload in step.)
 */
import {SuitePickKind} from './types';

/** The envelope `flash.call` resolves with: `result` when `ok`, `error` when not (the other fields are unused here). */
interface Reply {ok:boolean;result?:unknown;error?:{message:string;kind:string}}
interface Flash {
  call(op:string,args?:Record<string,unknown>):Promise<Reply>;
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
