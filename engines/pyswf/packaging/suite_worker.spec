# Standalone Python engine; the Electron host hides its console and supervises it.
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / 'packaging/suite_worker_entry.py')], pathex=[str(root)],
             datas=[(str(root / 'LICENSE'), '.')], binaries=[],
             hiddenimports=['py_swf.universal', 'py_swf.suite_cli', 'py_swf.suite_tools', 'py_swf.paths', 'py_swf.benchmarks',
                            'py_swf.benchmark_worker', 'py_swf.benchmark_rust_worker',
                            'py_swf.benchmark_external_worker'],
             excludes=['PySide6', 'webview', 'server'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name='FlashSuiteWorker', console=True, upx=False)
