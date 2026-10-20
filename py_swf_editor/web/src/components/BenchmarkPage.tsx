import { useEffect, useState } from "react";
import { ArrowLeft, BarChart3, Loader2, RefreshCw } from "lucide-react";

interface ReportInfo {
  id: string;
  profile: string;
  files: number;
  created_at: string;
  suitable_for_comparison: boolean;
  operation?: "source" | "disassemble";
}

export default function BenchmarkPage({ onClose }: { onClose: () => void }) {
  const [reports, setReports] = useState<ReportInfo[]>([]);
  const [selected, setSelected] = useState("overview");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetch("/api/benchmarks", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("No se pudieron cargar los informes.");
        return (await response.json()) as { reports: ReportInfo[] };
      })
      .then(({ reports: items }) => {
        setReports(items);
        setSelected((current) => current === "overview" || items.some((item) => item.id === current) ? current : "overview");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Error al cargar benchmarks.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  return (
    <section className="benchmark-view" aria-label="Laboratorio de benchmarks">
      <header className="benchmark-toolbar">
        <button className="text-button" onClick={onClose}><ArrowLeft size={16} />Volver al editor</button>
        <span className="benchmark-toolbar-title"><BarChart3 size={16} />Benchmarks</span>
        <label>Vista<select aria-label="Elegir informe de benchmarks" value={selected} disabled={loading} onChange={(event) => setSelected(event.target.value)}>
          <option value="overview">Comparación de todas las herramientas</option>
          {reports.map((report) => <option key={report.id} value={report.id}>{report.id} · {report.operation === "disassemble" ? "P-code" : "Fuente"} · {report.profile} · {report.files} SWF{!report.suitable_for_comparison ? " · exploratorio" : ""}</option>)}
        </select></label>
        <button className="icon-button" aria-label="Actualizar informes" disabled={loading} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={16} /></button>
      </header>
      {loading ? <div className="benchmark-state" role="status"><Loader2 size={20} className="animate-spin" />Cargando mediciones…</div>
        : error ? <div className="benchmark-state" role="alert">{error}<button className="text-button" onClick={() => setRefresh((value) => value + 1)}>Reintentar</button></div>
        : selected ? <iframe key={selected + refresh} title="Resultados de benchmarks de decompilación" src={`/api/benchmarks/${encodeURIComponent(selected)}/dashboard`} sandbox="allow-scripts allow-downloads" />
        : <div className="benchmark-state">Todavía no hay mediciones. Ejecuta el benchmark desde el entorno de desarrollo; esta página no lanza Java ni procesa archivos.</div>}
    </section>
  );
}
