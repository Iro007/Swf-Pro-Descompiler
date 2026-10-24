import {Highlight} from './model';
import {useFocus} from './shared';

/** The four leaders of the decompilers table. Only engines measured on the same cases compete; JPEXS, the reference, never does. */
export default function Highlights({cards}:{cards:Highlight[]}){
  const {set}=useFocus();
  return <ul className="bench-cards" aria-label="Destacados">
    {cards.map(card=><li key={card.id} className="bench-stat" data-empty={card.engine?undefined:'true'}
      onPointerEnter={()=>set(card.engine??null)} onPointerLeave={()=>set(null)}>
      <span className="bench-stat-label">{card.title}</span>
      <strong className="bench-stat-value">{card.value}</strong>
      {card.engine?<span className="bench-stat-engine"><i className={`bench-dot s${card.slot}`} aria-hidden="true"/>{card.name}</span>:null}
      <span className="bench-stat-sub">{card.sub}</span>
    </li>)}
  </ul>;
}
