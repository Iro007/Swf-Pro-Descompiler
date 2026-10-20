import { useState } from "react";
import { Code2, Loader2 } from "lucide-react";
import Dialog from "./Dialog";
import { createSwfFromSource } from "../api";
import { FileInfo } from "../types";
import { errorMessage } from "../utils/workspace";

const EXAMPLE=`package demo {
    public class Counter {
        private var total:int = 0;

        public function Counter(start:int = 0) {
            super();
            this.total = start;
        }

        public function add(n:int = 1):int {
            this.total = this.total + n;
            return this.total;
        }
    }
}`;

export default function ClassSourceDialog({onClose,onCreated}:{onClose:()=>void;onCreated:(file:FileInfo)=>void|Promise<void>}) {
  const [source,setSource]=useState(EXAMPLE);
  const [filename,setFilename]=useState("counter.swf");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  async function create() {
    if(busy) return;
    setBusy(true);setError("");
    try { await onCreated(await createSwfFromSource(source,filename)); }
    catch(reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }
  return <Dialog title="Crear SWF desde código AS3" onClose={()=>{if(!busy) onClose();}}>
    <p>Compila paquetes y clases que derivan de Object, con campos constantes, constructores, métodos y accesores. No ejecuta un juego ni depende de Java.</p>
    <label className="field-label">Nombre del archivo<input aria-label="Nombre del nuevo SWF" value={filename} onChange={event=>setFilename(event.target.value)} disabled={busy} /></label>
    <label className="field-label">Programa AS3<textarea aria-label="Programa AS3 nuevo" className="source-editor" style={{minHeight:360}} value={source} onChange={event=>setSource(event.target.value)} spellCheck={false} disabled={busy} /></label>
    <p className="section-diagnostic">El archivo actual no se modifica. Herencia personalizada, interfaces, inicializadores calculados y sintaxis no admitida se rechazan explícitamente.</p>
    {error && <p role="alert" className="inline-warning">{error}</p>}
    <div className="dialog-actions"><button className="studio-button secondary" disabled={busy} onClick={onClose}>Cancelar</button>
      <button className="studio-button primary" disabled={busy||!source.trim()||!filename.trim()} onClick={()=>void create()}>{busy?<Loader2 size={15} className="animate-spin"/>:<Code2 size={15}/>} {busy?"Compilando…":"Crear SWF"}</button>
    </div>
  </Dialog>;
}
