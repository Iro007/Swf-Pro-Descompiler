import {FolderOpen,Hammer,Package} from 'lucide-react';

/** Shown while no document is open. */
export default function StartPanel({busy,onOpen,onOpenProject,onCompile}:{busy:boolean;onOpen:()=>void;onOpenProject:()=>void;onCompile:()=>void}){
  return <div className="start-panel">
    <div className="start-card">
      <h1>Flash Universal Studio</h1>
      <p>Descompila, edita, compila y reproduce archivos SWF con varios motores en una sola ventana.</p>
      <div className="start-actions">
        <button type="button" className="tool-button primary large" disabled={busy} onClick={onOpen}><FolderOpen size={17}/>Abrir SWF…</button>
        <button type="button" className="tool-button large" disabled={busy} onClick={onOpenProject}><Package size={17}/>Abrir proyecto…</button>
        <button type="button" className="tool-button large" disabled={busy} onClick={onCompile}><Hammer size={17}/>Compilar desde fuentes…</button>
      </div>
      <p className="panel-muted">También puedes arrastrar un archivo a esta ventana · FWS · CWS · ZWS · .pyswfproj</p>
      <ul className="start-hints">
        <li><kbd>Ctrl</kbd>+<kbd>O</kbd> abre un SWF</li>
        <li><kbd>Ctrl</kbd>+<kbd>F</kbd> filtra el árbol</li>
        <li><kbd>Ctrl</kbd>+<kbd>Z</kbd> deshace el último cambio aplicado</li>
      </ul>
    </div>
  </div>;
}
