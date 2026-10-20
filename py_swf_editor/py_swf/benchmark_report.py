"""Read-only presentation of measured results; no worker or Java dependency."""
from __future__ import annotations

import html
import json
from pathlib import Path


def public_report(report):
    """Compact display snapshot: no local paths, generated source or raw bytecode."""
    result = {key: value for key, value in report.items() if key != "cases"}
    result["cases"] = []
    for case in report.get("cases", []):
        row = {key: value for key, value in case.items() if key not in ("file", "engines")}
        row["engines"] = [
            {key: value for key, value in engine.items() if key not in ("units", "documents")}
            for engine in case.get("engines", [])
        ]
        for engine in row["engines"]:
            if "compilation_audit" in engine:
                engine["compilation_audit"]={key:value for key,value in engine["compilation_audit"].items() if key!="records"}
                engine["compilation_audit"]["unit_records_in_full_report"]=True
        result["cases"].append(row)
    return result


def render_dashboard(report):
    rows = []
    def number(value):
        return "No medido" if value is None else f"{value:,.2f}"
    count = lambda value: "No verificado" if value is None else str(value)
    for case in report.get("cases", []):
        for engine in case.get("engines", []):
            quality = engine["quality"]
            rss = engine["hot"].get("peak_tree_rss_bytes")
            coverage = quality.get("disassembly_available") if report.get("operation")=="disassemble" else quality["source_available"]
            remote=report.get("measurement_context",{}).get("kind")=="remote_probe"
            status = engine.get("status","measured")
            engine_label = {"native":"PySWF", "python":"PySWF", "rust":"RustSWF"}.get(engine["engine"], engine["engine"]) + ({"not_configured":" · no configurado","not_applicable":" · no aplicable","output_unmapped":" · salida sin correspondencia","failed":" · fallo"}.get(status,""))
            audit=engine.get("compilation_audit",{}).get("summary")
            compilation=(f'{audit["validated_reopened"]}/{audit["targets"]} ({audit["eligible"]} admitidos; '+("métodos/acciones" if engine["engine"]=="native" else "clases/scripts/acciones")+")") if audit else f'{quality["compiled_verified"]}/{len(engine.get("compilation_sample", []))}'
            values = (case["relative"], engine_label, number(engine.get("request_latency_ms") if remote else engine["cold"].get("median_ms")),
                      number(engine["hot"].get("median_ms")), number(engine["hot"].get("p95_ms")),
                      number(rss / 1024**2 if rss is not None else None),
                      f'{count(coverage)}/{count(quality["expected_units_reported"])}',
                      count(quality["pcode"]), count(quality["failed"]),
                      compilation,
                      quality["behavior_verified"], len(engine.get("failures", [])))
            attrs = f' data-file="{html.escape(case["relative"], quote=True)}" data-engine="{html.escape(engine["engine"], quote=True)}"'
            rows.append("<tr" + attrs + ">" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>")
    embedded = json.dumps(public_report(report), ensure_ascii=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("benchmark_dashboard.html").read_text(encoding="utf-8")
    if report.get("configuration",{}).get("compile_documents"):
        template=template.replace("Muestra<br>recompilada","Auditoría<br>validada / objetivos")
        template=template.replace("Recompilación: muestra separada de hasta 8 documentos por archivo/motor. Python compila cuerpos de métodos; JPEXS compila script packs. No son denominadores equivalentes.","Recompilación: todos los objetivos quedan registrados. Python compila métodos/acciones; JPEXS compila clases/scripts/acciones. Sus denominadores son distintos.")
    if report.get("measurement_context",{}).get("kind")=="manual_export":
        template=template.replace("Menos suposiciones.","Exportación local importada.").replace("Más evidencia.","Sin medición de tiempo.")
    if report.get("operation")=="disassemble":
        template=template.replace("Fuente disponible<br>/ unidades","Desensamblado<br>/ unidades").replace("Fuente disponible (%)","Unidades con salida (%)")
    if report.get("measurement_context",{}).get("kind")=="remote_probe":
        template=template.replace("Frío<br>mediana (ms)","Solicitud remota<br>(ms)")
    return template.replace("__TABLE_ROWS__", "".join(rows)).replace("__REPORT_JSON__", embedded)
