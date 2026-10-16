#!/usr/bin/env python3
"""Compare Ganesh with the published RPP-SSP on checked-in GitHub datasets.

Usage (repo root):
    python3 benchmarks/compare_ganesh_repository_data.py \
       --repo . --ganesh ./ganesh_disjoint \
       --out benchmark_results/repository_data_comparison.csv

Original sources of preferences are unmodified; decompression is excluded from
per-algorithm computational time and included in a separate prep_s field.
"""
import argparse
import csv
import gzip
import os
import pathlib
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
RPP_R = re.compile(r'OK \(SSP\+Dijkstra\).*?min_cost\s*=\s*(\d+)', re.S)
GANESH_R = re.compile(r'^\s*(\d+)\s*$', re.M)
RPP_TIME = re.compile(r'Total program time:\s*(' + NUMBER + r')\s*s')
GANESH_TIME = re.compile(r'total_ms=(' + NUMBER + r')')
DATA = {
    'Bike': ('Bike_5000_instance.txt.gz', 5000),
    'Food': ('Food_5000_instance.txt.gz', 5000),
    'Hard': ('Hard_5000_instance.txt.gz', 5000),
    'NOR': ('NOR_5000_instance.txt.gz', 5000),
    'RAP': ('RAP_5000_instance.txt.gz', 5000),
    'TAXI': ('TAXI_5000_instance.txt.gz', 5000),
    'test_case': ('test_case.txt', 7),
}
FIELDS = ('dataset','n','repeat','algorithm','r','compute_s','wall_s',
          'peak_mib','prep_s','status','agreement','valid','reason')


def cap_memory(limit_mib):
    def fn():
        limit = limit_mib * (1 << 20)
        resource.setrlimit(resource.RLIMIT_AS, (limit,limit))
    return fn


def timed_run(name, command, cwd, stdin_file, seconds, limit_mib):
    with tempfile.NamedTemporaryFile(prefix='rss_', delete=False) as fh:
        path = pathlib.Path(fh.name)
    command = ['/usr/bin/time', '-f', '%M', '-o', str(path)] + command
    try:
        with open(stdin_file,'rb') if stdin_file else open(os.devnull,'rb') as stdin:
            start = time.perf_counter()
            cp = subprocess.run(command, cwd=cwd, stdin=stdin,
                                capture_output=True, text=True, timeout=seconds,
                                preexec_fn=cap_memory(limit_mib))
            wall = time.perf_counter() - start
        if cp.returncode:
            raise RuntimeError(f'exit {cp.returncode}: {cp.stdout[-400:]} {cp.stderr[-400:]}')
        m=(GANESH_R if name=='ganesh' else RPP_R).search(cp.stdout)
        if m is None: raise RuntimeError(f'missing optimum: {cp.stdout[-500:]}')
        r=int(m.group(1))
        t=(GANESH_TIME.search(cp.stderr) if name=='ganesh' else RPP_TIME.search(cp.stdout))
        if t is None: raise RuntimeError(f'missing internal time: {cp.stdout[-300:]} {cp.stderr[-300:]}')
        tval=float(t.group(1))/(1000 if name=='ganesh' else 1)
        rss_txt=path.read_text().strip().splitlines()[0]
        mem=float(rss_txt)/1024
        return {'r':r,'compute_s':tval,'wall_s':wall,'peak_mib':mem}
    finally:
        path.unlink(missing_ok=True)


def prepare(source, raw, ganesh, n):
    start=time.perf_counter()
    with (gzip.open(source,'rb') if source.suffix=='.gz' else source.open('rb')) as inp, raw.open('wb') as out:
        shutil.copyfileobj(inp,out,1024*1024)
    with ganesh.open('wb') as out,raw.open('rb') as inp:
        out.write(f'{n}\n'.encode())
        shutil.copyfileobj(inp,out,1024*1024)
    # Confirm first and last records and that there are precisely 2n lines.
    count=0
    with raw.open('rb') as fh:
        for line in fh:
            if not line.strip(): continue
            count+=1
            if count<=2:
                assert len(line.split())==n, f'Invalid {source}: preference row has wrong number of entries'
    if count != 2*n:
        raise ValueError(f'Invalid {source}: expected {2*n} preference rows, found {count}')
    return time.perf_counter()-start


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=pathlib.Path,required=True)
    ap.add_argument('--ganesh',type=pathlib.Path,required=True)
    ap.add_argument('--data-dir',type=pathlib.Path)
    ap.add_argument('--datasets',default='Bike,Food,Hard,NOR,RAP,TAXI,test_case')
    ap.add_argument('--repeats',type=int,default=2)
    ap.add_argument('--timeout',type=int,default=600)
    ap.add_argument('--max-vmem-mib',type=int,default=5120)
    ap.add_argument('--out',type=pathlib.Path,default=pathlib.Path('benchmark_results/repository_data_comparison.csv'))
    args=ap.parse_args()
    if args.repeats<1 or args.timeout<1: ap.error('repeats and timeout must be positive')
    repo=args.repo.resolve()
    data_dir=(args.data_dir or repo/'data').resolve()
    ganesh=args.ganesh.resolve()
    ssp=(repo/'ssp_dijkstra').resolve()
    datasets=[v.strip() for v in args.datasets.split(',') if v.strip()]
    if not datasets or any(v not in DATA for v in datasets): ap.error(f'Unsupported datasets {datasets}; allowed {list(DATA)}')
    for binary in (ganesh,ssp):
        if not binary.is_file() or not os.access(binary,os.X_OK): ap.error(f'Missing executable: {binary}')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    failures=0; complete=0
    with args.out.open('w',newline='') as output, tempfile.TemporaryDirectory(prefix='github_data_') as t:
        csvout=csv.DictWriter(output,fieldnames=FIELDS)
        csvout.writeheader();output.flush()
        work=pathlib.Path(t)
        for dataset in datasets:
            fname,n=DATA[dataset]
            source=data_dir/fname
            print(f'START dataset={dataset} n={n} compressed_size={source.stat().st_size if source.exists() else -1}',flush=True)
            if not source.exists():
                failures+=1;print(f'FAILED dataset={dataset} file missing: {source}',flush=True)
                continue
            raw=work/'prefs.txt'; gin=work/'ganesh_input.txt'
            try:
                prep_s=prepare(source,raw,gin,n)
            except Exception as exc:
                failures+=1
                print(f'FAILED dataset={dataset} could not prepare input: {exc}',flush=True)
                continue
            for rep in range(args.repeats):
                records=[]
                # Reverse order every second repeat to avoid a fixed cache bias.
                order=('ganesh','ssp') if rep%2==0 else ('ssp','ganesh')
                for algorithm in order:
                    row=dict(dataset=dataset,n=n,repeat=rep,algorithm=algorithm,
                             r='',compute_s='',wall_s='',peak_mib='',prep_s=prep_s,
                             status='failed',agreement=False,valid=False,reason='')
                    if algorithm=='ganesh':
                        command=[str(ganesh),'--timings','--skip-check']; stdin=gin
                    else:
                        command=[str(ssp),str(raw),str(n),'1']; stdin=None
                    try:
                        row.update(timed_run(algorithm,command,work,stdin,args.timeout,args.max_vmem_mib))
                        row['status']='ok'
                    except Exception as exc:
                        failures+=1;row['reason']=str(exc)[:500]
                        print(f'FAILED dataset={dataset} rep={rep} {algorithm}: {exc}',flush=True)
                    records.append(row)
                succeeded=[r for r in records if r['status']=='ok']
                agreement=len(succeeded)==2 and succeeded[0]['r']==succeeded[1]['r']
                if agreement and dataset=='test_case' and succeeded[0]['r']!=2:
                    agreement=False
                if not agreement:failures+=1
                else:complete+=1
                for row in records:
                    row['agreement']=agreement;row['valid']=agreement
                    csvout.writerow(row)
                output.flush()
                print(('OK' if agreement else 'MISMATCH/FAILED'),f'dataset={dataset} rep={rep}',
                      ' '.join(f'{r["algorithm"]}:r={r["r"]},time={r["compute_s"]},mem={r["peak_mib"]}' for r in records),flush=True)
            raw.unlink(missing_ok=True); gin.unlink(missing_ok=True)
    print(f'FINISHED verified paired comparisons={complete}, failures/mismatches={failures}',flush=True)
    if failures:sys.exit(1)

if __name__=='__main__': main()
