import {ReactNode} from 'react';
import Dialog from '../../components/Dialog';
import SuiteBenchmarks from '../../components/SuiteBenchmarks';

export function BenchmarksDialog({onClose}:{onClose:()=>void}){
  return <Dialog title="Benchmarks" onClose={onClose} size="full"><SuiteBenchmarks/></Dialog>;
}

export function AboutDialog({onClose}:{onClose:()=>void}){
  return <Dialog title="Flash Universal Studio" onClose={onClose} size="sm">
    <p>Suite de descompilación, edición y compilación de SWF con PySWF, RustSWF y herramientas externas, en una sola ventana.</p>
    <p>Abre un archivo, elige un elemento del árbol y trabaja con su código, recursos o bytes. Cada edición se valida y conserva el original.</p>
    <p className="panel-muted">GPL-3.0-or-later · lector de tags Ruffle.</p>
  </Dialog>;
}

export function ConfirmDialog({title,children,confirmLabel,onConfirm,onClose}:{title:string;children:ReactNode;confirmLabel:string;onConfirm:()=>void;onClose:()=>void}){
  return <Dialog title={title} onClose={onClose} size="sm" footer={<>
    <button type="button" className="tool-button" onClick={onClose}>Cancelar</button>
    <button type="button" className="tool-button primary" onClick={()=>{onClose();onConfirm();}}>{confirmLabel}</button></>}>
    {children}
  </Dialog>;
}
