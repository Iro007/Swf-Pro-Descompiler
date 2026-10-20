"""Bounded, read-only catalog latency/SQL-count probe; never purges OS caches."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def main():
    import psutil
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--implementation',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--capture-summary',type=Path)
    parser.add_argument('--campaign')
    parser.add_argument('--deadline',type=float,default=45)
    parser.add_argument('--repeats',type=int,default=2)
    args=parser.parse_args()
    if args.deadline<=0 or not 1<=args.repeats<=5:
        parser.error('deadline must be positive and repeats must be 1..5')
    if args.implementation:
        spec=importlib.util.spec_from_file_location('py_swf.catalog_latency_baseline',args.implementation.resolve())
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    else:
        import py_swf.benchmark_catalog as module
    implementation=Path(module.__file__).resolve()
    original_connect=sqlite3.connect
    process=psutil.Process()
    records=[]
    try:
        for repeat in range(args.repeats):
            counts=Counter();connections=[];started=time.perf_counter();deadline=started+args.deadline
            before_rss=process.memory_info().rss
            def connect(database,*positional,**options):
                connection=original_connect(database,*positional,**options)
                connections.append({'uri':str(database),'uri_enabled':options.get('uri',False)})
                def trace(sql):
                    normalized=' '.join(sql.upper().split())
                    counts['all']+=1
                    if ' FROM RUNS ' in ' '+normalized+' ':counts['runs_reads']+=1
                    if 'COUNT(' in normalized:counts['counts']+=1
                    if normalized.startswith(('INSERT','UPDATE','DELETE','CREATE','DROP','ALTER','REPLACE')):counts['writes']+=1
                connection.set_trace_callback(trace)
                connection.set_progress_handler(lambda:int(time.perf_counter()>deadline),10000)
                return connection
            module.sqlite3.connect=connect
            filters={'campaign':args.campaign} if args.campaign else {}
            status,error,summary='completed',None,None
            try:summary=module.catalog_summary(args.database,**filters)
            except sqlite3.OperationalError as exc:status,error='interrupted',str(exc)
            elapsed=time.perf_counter()-started
            record={'repeat':repeat,'cache_context':'first process call; OS/page cache not purged' if repeat==0 else 'same-process repeat; OS/page cache not purged',
                    'status':status,'error':error,'catalog_seconds':elapsed,'sql_statements':dict(counts),
                    'rss_before_bytes':before_rss,'rss_after_bytes':process.memory_info().rss,
                    'available_ram_bytes':psutil.virtual_memory().available,'connections':connections,'filters':filters}
            if summary is not None:
                digest=hashlib.sha256();capture=args.capture_summary if repeat==0 else None
                if capture:capture.parent.mkdir(parents=True,exist_ok=True)
                handle=capture.open('w',encoding='utf-8') if capture else None
                try:
                    for text in json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(',',':')).iterencode(summary):
                        digest.update(text.encode('utf-8'))
                        if handle:handle.write(text)
                finally:
                    if handle:handle.close()
                record.update(summary_sha256=digest.hexdigest(),row_count=len(summary['rows']),campaign_count=len(summary['campaigns']),totals=summary['totals'])
            records.append(record)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'implementation':str(implementation),'implementation_sha256':hashlib.sha256(implementation.read_bytes()).hexdigest(),
                'database':str(args.database.resolve()),'database_bytes':args.database.stat().st_size,'deadline_seconds':args.deadline,
                'cache_policy':'No cache purge. First-process and repeated calls are explicitly labelled; no cold-cache claim.',
                'records':records},indent=2),encoding='utf-8')
            print(json.dumps({key:record[key] for key in ('repeat','status','catalog_seconds','sql_statements','error')}),flush=True)
            del summary
    finally:module.sqlite3.connect=original_connect


if __name__=='__main__':main()
