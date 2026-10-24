import {ReactNode,useEffect,useId,useRef} from 'react';
import {X} from 'lucide-react';

type Size='sm'|'md'|'lg'|'xl'|'full';

/** Modal dialog on the native <dialog> element: focus is trapped and Escape closes it. */
export default function Dialog({title,onClose,children,size='md',footer}:{title:string;onClose:()=>void;children:ReactNode;size?:Size;footer?:ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  const titleId=useId();
  useEffect(()=>{
    const dialog=ref.current;
    if(dialog&&!dialog.open)dialog.showModal();
    return()=>{if(dialog?.open)dialog.close();};
  },[]);
  return (
    <dialog ref={ref} className={`modal modal-${size}`} aria-labelledby={titleId} onCancel={event=>{event.preventDefault();onClose();}}
      onMouseDown={event=>{if(event.target===ref.current)onClose();}}>
      <header className="modal-header">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="icon-button" aria-label="Cerrar diálogo" onClick={onClose}><X size={16}/></button>
      </header>
      <div className="modal-body">{children}</div>
      {footer?<footer className="modal-footer">{footer}</footer>:null}
    </dialog>
  );
}
