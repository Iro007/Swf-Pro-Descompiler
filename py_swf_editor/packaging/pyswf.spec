# PyInstaller spec for the Python/web portion of the Windows package.
# Native distribution: embeds Python and the UI. No JRE/JPEXS is included.
from pathlib import Path

root = Path(SPECPATH).parent
web_dist = root / "web" / "dist"
data_files = [(str(web_dist), "web/dist"), (str(root / "LICENSE"), "."),
              (str(root / "py_swf/benchmark_dashboard.html"), "py_swf")]
data_files.append((str(root / "py_swf/benchmark_overview.html"),"py_swf"))
catalog=root / "reports/tool-catalog.json"
if catalog.is_file():
    data_files.append((str(catalog),"benchmark_data"))
snapshot = root / "reports/benchmark-release/benchmark.json"
if snapshot.is_file():
    data_files.append((str(snapshot), "benchmark_data"))
for report_id in ("disassembly","remote","audit"):
    additional=root / f"reports/benchmark-release-{report_id}/benchmark.json"
    if additional.is_file():
        data_files.append((str(additional),f"benchmark_data/{report_id}"))

a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(root)],
    binaries=[],
    datas=data_files,
    hiddenimports=["server.app", "server.routers.files", "server.routers.export", "server.routers.benchmarks"],
    noarchive=False,
)
# pywebview's hook collects an Android helper JAR on every platform.
# Windows uses WebView2/.NET; never ship dormant Java artifacts here.
a.datas = [entry for entry in a.datas if not entry[0].lower().endswith(".jar")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="PySWFStudio",
    console=False,
    upx=False,
)
