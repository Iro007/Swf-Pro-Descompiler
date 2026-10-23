import {KeyboardEvent,useEffect,useRef,useState} from 'react';
import {Check} from 'lucide-react';

export interface MenuItem {id:string;label?:string;shortcut?:string;disabled?:boolean;checked?:boolean;separator?:boolean;title?:string;onSelect?:()=>void}
export interface MenuDef {id:string;label:string;items:MenuItem[]}

/** Desktop-style menu bar: click or hover to open, arrow keys to move, Escape to close. */
export default function MenuBar({menus}:{menus:MenuDef[]}){
  const [open,setOpen]=useState<string|null>(null);
  const bar=useRef<HTMLDivElement>(null);

  useEffect(()=>{
    if(!open)return;
    const away=(event:PointerEvent)=>{if(!bar.current?.contains(event.target as Node))setOpen(null);};
    const blur=()=>setOpen(null);
    document.addEventListener('pointerdown',away);window.addEventListener('blur',blur);
    return()=>{document.removeEventListener('pointerdown',away);window.removeEventListener('blur',blur);};
  },[open]);

  const focusItem=(menuId:string,index:number)=>{
    const items=Array.from(bar.current?.querySelectorAll<HTMLButtonElement>(`[data-menu="${menuId}"] .menu-item:not(:disabled)`)??[]);
    if(!items.length)return;
    items[(index+items.length)%items.length]?.focus();
  };
  const onMenuKey=(event:KeyboardEvent<HTMLElement>,menuIndex:number)=>{
    const menu=menus[menuIndex];
    const items=Array.from(bar.current?.querySelectorAll<HTMLButtonElement>(`[data-menu="${menu.id}"] .menu-item:not(:disabled)`)??[]);
    const current=items.indexOf(document.activeElement as HTMLButtonElement);
    if(event.key==='ArrowDown'){event.preventDefault();focusItem(menu.id,current+1);}
    else if(event.key==='ArrowUp'){event.preventDefault();focusItem(menu.id,current<0?-1:current-1);}
    else if(event.key==='Home'){event.preventDefault();focusItem(menu.id,0);}
    else if(event.key==='End'){event.preventDefault();focusItem(menu.id,-1);}
    else if(event.key==='Escape'){event.preventDefault();setOpen(null);bar.current?.querySelector<HTMLButtonElement>(`[data-top="${menu.id}"]`)?.focus();}
    else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
      event.preventDefault();
      const next=menus[(menuIndex+(event.key==='ArrowRight'?1:-1)+menus.length)%menus.length];
      setOpen(next.id);setTimeout(()=>focusItem(next.id,0),0);
    }
  };

  return <div className="menubar" role="menubar" aria-label="Menú principal" ref={bar}>
    {menus.map((menu,menuIndex)=><div key={menu.id} className="menu" data-menu={menu.id}>
      <button type="button" className={`menu-top ${open===menu.id?'open':''}`} data-top={menu.id} role="menuitem" aria-haspopup="menu" aria-expanded={open===menu.id}
        onClick={()=>setOpen(open===menu.id?null:menu.id)} onPointerEnter={()=>{if(open&&open!==menu.id)setOpen(menu.id);}}
        onKeyDown={event=>{if(event.key==='ArrowDown'||event.key==='Enter'||event.key===' '){event.preventDefault();setOpen(menu.id);setTimeout(()=>focusItem(menu.id,0),0);}else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();const next=menus[(menuIndex+(event.key==='ArrowRight'?1:-1)+menus.length)%menus.length];bar.current?.querySelector<HTMLButtonElement>(`[data-top="${next.id}"]`)?.focus();if(open)setOpen(next.id);}}}>
        {menu.label}
      </button>
      {open===menu.id?<div className="menu-list" role="menu" aria-label={menu.label} onKeyDown={event=>onMenuKey(event,menuIndex)}>
        {menu.items.map(item=>item.separator?<div key={item.id} className="menu-separator" role="separator"/>
          :<button type="button" key={item.id} role={item.checked!==undefined?'menuitemradio':'menuitem'} aria-checked={item.checked} className="menu-item" disabled={item.disabled} title={item.title}
            onClick={()=>{setOpen(null);item.onSelect?.();}}>
            <span className="menu-check">{item.checked?<Check size={13}/>:null}</span>
            <span className="menu-label">{item.label}</span>
            {item.shortcut?<kbd>{item.shortcut}</kbd>:null}
          </button>)}
      </div>:null}
    </div>)}
  </div>;
}
