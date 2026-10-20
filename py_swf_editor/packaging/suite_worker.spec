# Standalone Python engine; the Tauri host hides its console and supervises it.
from pathlib import Path
import os
root = Path(SPECPATH).parent
catalog = root.parent / 'reports/benchmark-catalog/benchmark.sqlite'
catalog_data = [(str(catalog), 'benchmark_data')] if catalog.is_file() and os.environ.get('FLASH_SUITE_CATALOG_EXTERNAL') != '1' else []
a = Analysis([str(root / 'packaging/suite_worker_entry.py')], pathex=[str(root)],
             datas=[(str(root / 'LICENSE'), '.'),
                    (str(root / 'py_swf/benchmark_dashboard.html'), 'py_swf'),
                    (str(root / 'py_swf/benchmark_overview.html'), 'py_swf')] + catalog_data, binaries=[],
             hiddenimports=['py_swf.universal', 'py_swf.suite_cli', 'py_swf.suite_tools', 'py_swf.benchmarks',
                            'py_swf.benchmark_worker', 'py_swf.benchmark_rust_worker',
                            'py_swf.benchmark_external_worker'],
             excludes=['PySide6', 'webview', 'server'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name='FlashSuiteWorker', console=True, upx=False)
