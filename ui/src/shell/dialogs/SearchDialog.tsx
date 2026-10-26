import {useState, useMemo, useRef, useEffect} from 'react';
import {Search, Code2, Tag, FileText, ArrowRight} from 'lucide-react';
import Dialog from '../../components/Dialog';
import {TreeNode} from '../../types';
import {pathTo} from '../treeModel';

export interface SearchResult {
  id: string;
  node: TreeNode;
  title: string;
  pathText: string;
  matchType: 'name' | 'member' | 'tag';
  snippet?: string;
}

export default function SearchDialog({
  nodes,
  onSelect,
  onClose,
}: {
  nodes: TreeNode[];
  onSelect: (node: TreeNode) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [caseSensitive, setCaseSensitive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const results = useMemo(() => {
    const q = query.trim();
    if (!q) return [];
    const needle = caseSensitive ? q : q.toLowerCase();
    const hits: SearchResult[] = [];

    function searchNode(node: TreeNode) {
      const label = caseSensitive ? node.label : node.label.toLowerCase();
      const nodePath = pathTo(nodes, node.id) ?? [node];
      const pathText = nodePath.map(n => n.label).join(' > ');

      if (label.includes(needle)) {
        hits.push({
          id: node.id,
          node,
          title: node.label,
          pathText,
          matchType: node.kind === 'class' || node.kind === 'script' ? 'name' : 'tag',
        });
      }

      if (node.members) {
        for (const member of node.members) {
          const mName = caseSensitive ? member.name : member.name.toLowerCase();
          if (mName.includes(needle)) {
            hits.push({
              id: `${node.id}:${member.unit_id}`,
              node,
              title: `${node.label} :: ${member.name}`,
              pathText: `${pathText} > ${member.name} (${member.role})`,
              matchType: 'member',
              snippet: `Miembro: ${member.name} (${member.role})`,
            });
          }
        }
      }

      if (node.children) {
        for (const child of node.children) {
          searchNode(child);
        }
      }
    }

    for (const root of nodes) {
      searchNode(root);
    }
    return hits.slice(0, 200);
  }, [nodes, query, caseSensitive]);

  return (
    <Dialog title="Buscar en el documento (Ctrl+Mayús+F)" onClose={onClose} size="lg">
      <div className="search-dialog" style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
        <div style={{display: 'flex', gap: '8px', alignItems: 'center'}}>
          <div style={{position: 'relative', flex: 1}}>
            <input
              ref={inputRef}
              type="text"
              className="text-input"
              style={{width: '100%', paddingLeft: '28px'}}
              placeholder="Buscar etiquetas, clases, funciones, miembros…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && results.length > 0) {
                  onSelect(results[0].node);
                  onClose();
                }
              }}
            />
            <Search size={15} style={{position: 'absolute', left: '8px', top: '50%', transform: 'translateY(-50%)', opacity: 0.5}} />
          </div>
          <label style={{display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px', cursor: 'pointer'}}>
            <input
              type="checkbox"
              checked={caseSensitive}
              onChange={e => setCaseSensitive(e.target.checked)}
            />
            Mayús/minús
          </label>
        </div>

        <div style={{fontSize: '12px', color: 'var(--fg-dim)'}}>
          {query.trim()
            ? `${results.length} resultado${results.length === 1 ? '' : 's'}${results.length >= 200 ? ' (mostrando los primeros 200)' : ''}`
            : 'Escribe para buscar etiquetas, clases o métodos en todo el archivo.'}
        </div>

        <div
          style={{
            maxHeight: '380px',
            overflowY: 'auto',
            border: '1px solid var(--line)',
            borderRadius: '4px',
            background: 'var(--code-bg)',
          }}
        >
          {results.length === 0 && query.trim() ? (
            <div style={{padding: '24px', textAlign: 'center', color: 'var(--fg-dim)'}}>
              No se encontraron coincidencias para "{query}".
            </div>
          ) : null}
          {results.map(item => (
            <div
              key={item.id}
              onClick={() => {
                onSelect(item.node);
                onClose();
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 12px',
                borderBottom: '1px solid var(--line)',
                cursor: 'pointer',
                gap: '12px',
              }}
              className="search-result-row"
            >
              <div style={{display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden'}}>
                {item.matchType === 'member' ? (
                  <Code2 size={15} style={{color: 'var(--syn-function)', flexShrink: 0}} />
                ) : item.matchType === 'name' ? (
                  <FileText size={15} style={{color: 'var(--syn-keyword)', flexShrink: 0}} />
                ) : (
                  <Tag size={15} style={{color: 'var(--syn-type)', flexShrink: 0}} />
                )}
                <div style={{overflow: 'hidden'}}>
                  <div style={{fontWeight: 500, whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden'}}>
                    {item.title}
                  </div>
                  <div style={{fontSize: '11px', color: 'var(--fg-dim)', whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden'}}>
                    {item.pathText}
                  </div>
                </div>
              </div>
              <ArrowRight size={14} style={{opacity: 0.4, flexShrink: 0}} />
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
