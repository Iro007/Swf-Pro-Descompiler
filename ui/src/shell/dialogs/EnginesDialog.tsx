import {useEffect,useState} from 'react';
import {Play,Settings2} from 'lucide-react';
import * as api from '../../api';
import {BenchmarkEngine,SuiteCatalog,SuiteEngine} from '../../types';
import Dialog from '../../components/Dialog';
import {displayEngineName} from '../../displayNames';
import {PathField} from '../fields';
import {Session} from '../useSession';
import {problem} from '../util';

const operationLabels:Record<string,string>={decompile:'Descompilar',edit:'Editar fuente',assemble:'Ensamblar P-code',compile_project:'Compilar proyecto','resource-bundle':'Recursos',scripts:'Scripts',xml:'XML',fla:'FLA',xfl:'XFL',launch:'Abrir herramienta',disassemble:'Desensamblar',resources:'Listar recursos',resource_edit:'Editar recursos',timeline:'Cuadros',preview:'Reproducir',benchmark:'Medir corpus',source_edit:'Editar fuente guardada',compile_saved:'Recompilar fuentes',resource_info:'Inspeccionar recurso',capabilities:'Capacidades',abc_metadata:'Metadatos ABC',roundtrip:'Ida y vuelta',extract:'Extraer',import_result:'Importar resultado'};

/** Engines and tools known to the suite (and the ones only the benchmark compares), with their local paths. */
export default function EnginesDialog({session,onClose}:{session:Session;onClose:()=>void}){
  const {catalog,busy}=session;
  const [paths,setPaths]=useState<Record<string,string>>({});
  const [comparison,setComparison]=useState<BenchmarkEngine[]>([]);
  const disabled=!!busy||!api.native;

  useEffect(()=>{
    if(!api.native)return;
    let current=true;
    api.suiteRpc<{engines:BenchmarkEngine[]}>('benchmark_tools').then(result=>{if(current)setComparison(result.engines);}).catch(()=>undefined);
    return()=>{current=false;};
  },[]);

  const configure=async(tool:{id:string;name:string})=>{
    const next=await session.run(`Configurando ${displayEngineName(tool.id,tool.name)}…`,()=>api.suiteRpc<SuiteCatalog>('configure_engine',{id:tool.id,path:(paths[tool.id]??'').trim()}));
    if(next){session.setCatalog(next);session.notify(`Configuración actualizada: ${displayEngineName(tool.id,tool.name)}. Consulta su disponibilidad y diagnóstico.`);}
  };
  const refresh=()=>void session.run('Comprobando herramientas…',async()=>{await session.refreshCatalog();});
  const launch=(tool:SuiteEngine)=>void session.run(`Abriendo ${displayEngineName(tool.id,tool.name)}…`,()=>api.suiteRpc('launch_tool',{id:tool.id}));
  const unmeasured=comparison.filter(item=>!item.configured);

  return <Dialog title="Motores y herramientas" onClose={onClose} size="xl"
    footer={<><span className="dim">La disponibilidad proviene del backend local.</span><button type="button" className="tool-button" disabled={disabled} onClick={refresh}><Settings2 size={14}/>Comprobar disponibilidad</button></>}>
    <p className="panel-muted">Configura el ejecutable, JAR o herramienta del SDK. Las herramientas comerciales conservan su licencia y trabajan mediante asistencia o importación de sus resultados.</p>
    <div className="tool-grid">
      {catalog?.engines.map(tool=><article className="card" key={tool.id}>
        <header className="card-heading"><h3>{displayEngineName(tool.id,tool.name)}</h3><span className={`badge ${tool.available?'good':''}`}>{tool.available?'Disponible':'Sin configurar'}</span></header>
        <p className="panel-muted small">{tool.version?`Versión ${tool.version} · `:''}{tool.category}</p>
        <p className="tool-detail">{tool.id==='python'?'PySWF está integrado en la aplicación. ':''}{tool.detail}</p>
        <div className="chips">{tool.operations.length?tool.operations.map(operation=><span className="chip" key={operation}>{operationLabels[operation]??operation}</span>):<span className="chip">Sin operaciones disponibles</span>}</div>
        <PathField label={`Ruta de ${displayEngineName(tool.id,tool.name)}`} value={paths[tool.id]??tool.path??''} onChange={value=>setPaths(previous=>({...previous,[tool.id]:value}))} kind="tool" disabled={disabled||tool.id==='python'}/>
        <div className="button-row">
          <button type="button" className="tool-button" disabled={disabled||tool.id==='python'||!(paths[tool.id]??'').trim()} onClick={()=>void configure(tool)}>Guardar configuración</button>
          {tool.operations.includes('launch')?<button type="button" className="tool-button" disabled={disabled||!tool.available} onClick={()=>launch(tool)}><Play size={13}/>Abrir herramienta</button>:null}
        </div>
        {tool.category==='assisted'?<p className="panel-muted small">Utiliza el archivo exportado por el proveedor para su revisión. Este motor no se considera medido hasta importar y verificar sus resultados.</p>:null}
      </article>)}
    </div>
    {!catalog?<div className="empty-note"><p>No se pudo consultar el catálogo de motores.</p><button type="button" className="tool-button" disabled={disabled} onClick={refresh}>Reintentar</button></div>:null}
    {comparison.length?<section className="card spaced">
      <h3>Motores de comparación (benchmarks)</h3>
      <p className="panel-muted">Estado local de todos los motores que mide el banco de pruebas, incluidos los que la ventana no usa para editar.</p>
      <table className="data-table"><thead><tr><th scope="col">Motor</th><th scope="col">Categoría</th><th scope="col">Estado</th></tr></thead>
        <tbody>{comparison.map(item=><tr key={item.id}><td>{displayEngineName(item.id==='native'?'python':item.id,item.name)}</td><td className="dim">{item.category}</td><td>{item.configured?<span className="badge good">Configurado</span>:<span className="badge" title={item.setup_diagnostic}>{item.setup_diagnostic?problem(item.setup_diagnostic).slice(0,80):'Sin configurar'}</span>}</td></tr>)}</tbody>
      </table>
      {unmeasured.length?<p className="panel-muted small">{unmeasured.length} motor{unmeasured.length===1?'':'es'} sin medir en este equipo.</p>:null}
    </section>:null}
  </Dialog>;
}
