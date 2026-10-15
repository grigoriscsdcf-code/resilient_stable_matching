#!/usr/bin/env python3
"""Benchmark the ACTUAL PXBian executables against Ganesh on identical input.

Requires PXBian/resilient_stable_matching built with `make` and the provided
`ganesh_disjoint` binary compiled alongside. Reports full-program time (including
input parsing and preparation) as instrumented by both projects, plus peak RSS.
No estimates or substitutions.
"""
import argparse, csv, pathlib, random, re, statistics, subprocess, tempfile, time, os, sys

PARSER = {
    'ssp': re.compile(r'OK \(SSP\+Dijkstra\).*?min_cost\s*=\s*(\d+)', re.S),
    'ffk': re.compile(r'OK \(Competitor Def1 Dual\).*?total_cost\s*=\s*(\d+)', re.S),
    'ganesh': re.compile(r'^\s*(\d+)\s*$', re.M),
}

def generate(n, mode, seed, path):
    rng = random.Random(seed)
    with path.open('w', buffering=1024*1024) as f:
        for side in range(2):
            for p in range(n):
                if mode == 'cyclic':
                    row = ((p+(1 if side else 0)+j) % n for j in range(n))
                elif mode == 'identical':
                    row = range(n)
                elif mode == 'random':
                    arr = list(range(n)); rng.shuffle(arr); row = arr
                else:
                    raise ValueError(mode)
                f.write(' '.join(map(str, row)) + '\n')

def timed_run(name, args, cwd, stdin_path, timeout):
    with tempfile.NamedTemporaryFile(prefix='rpp_rss_', delete=False) as rf:
        rss_path = rf.name
    command=['/usr/bin/time','-f','%M','-o',rss_path] + list(map(str,args))
    try:
        with (open(stdin_path,'rb') if stdin_path else open(os.devnull, 'rb')) as fd:
            start=time.perf_counter()
            cp=subprocess.run(command, cwd=cwd, stdin=fd, capture_output=True, text=True, timeout=timeout, check=False)
            wall=time.perf_counter()-start
        rss=pathlib.Path(rss_path).read_text().strip()
        if cp.returncode:
            raise RuntimeError(f'{name}: exit {cp.returncode}\n{cp.stdout[-3000:]}\n{cp.stderr[-3000:]}')
        out=cp.stdout
        m=PARSER[name].search(out)
        if not m:
            raise RuntimeError(f'{name}: cannot parse optimum from output\n{out[-3000:]}')
        value=int(m.group(1))
        if name=='ganesh':
            tm=re.search(r'total_ms=([\d.]+)',cp.stderr)
            if not tm: raise RuntimeError(f'Ganesh timing missing: {cp.stderr}')
            internal=float(tm.group(1))/1000
        else:
            mm=re.search(r'Total program time:\s*([\d.]+)\s*s', out)
            if mm is None: raise RuntimeError(f'{name}: internal timing missing')
            internal=float(mm.group(1))
        return dict(value=value, compute_s=internal, wall_s=wall, peak_mib=int(rss)/1024)
    finally:
        pathlib.Path(rss_path).unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=pathlib.Path, help='Locally built PXBian repository')
    p.add_argument('--ganesh', required=True, type=pathlib.Path)
    p.add_argument('--out', default='rpp_ganesh_three_way.csv')
    p.add_argument('--sizes', default='50,100,300,1000,3000')
    p.add_argument('--modes', default='random,cyclic,identical')
    p.add_argument('--seeds', default='41,42,43')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--timeout', type=int, default=600)
    args=p.parse_args()
    repo=args.repo.resolve(); ganesh=args.ganesh.resolve()
    executables={'ganesh':ganesh,'ssp':repo/'ssp_dijkstra','ffk':repo/'competitor'}
    for name,path in executables.items():
        if not path.is_file() or not os.access(path,os.X_OK):
            p.error(f'Missing executable {name}: {path}. Build your repository (`make`) and Ganesh first.')
    sizes=[int(x) for x in args.sizes.split(',')]; modes=args.modes.split(','); seeds=[int(x) for x in args.seeds.split(',')]
    rows=[]; failures=0
    with tempfile.TemporaryDirectory(prefix='rpp_comparison_') as td:
        td=pathlib.Path(td)
        for n in sizes:
            for mode in modes:
                for seed in (seeds if mode=='random' else [0]):
                    raw=td/f'{mode}_{n}_{seed}.txt'
                    gin=td/f'{mode}_{n}_{seed}_ganesh.txt'
                    generate(n,mode,seed,raw)
                    with gin.open('wb') as f, raw.open('rb') as src:
                        f.write((str(n)+'\n').encode())
                        while b:=src.read(1<<20): f.write(b)
                    for repeat in range(args.repeats):
                        results={}
                        for name in ('ganesh','ssp','ffk'):
                            if name=='ganesh':
                                command=[str(executables[name]),'--timings','--skip-check']
                                inf=gin
                                cwd=td
                            else:
                                command=[str(executables[name]),str(raw),str(n)]+(['1'] if name=='ssp' else [])
                                inf=None
                                cwd=td
                            try:
                                result=timed_run(name,command,cwd,inf,args.timeout)
                                results[name]=result
                                rec=dict(mode=mode,n=n,seed=seed,repeat=repeat,algorithm=name,**result,valid=None)
                                rows.append(rec)
                            except Exception as exc:
                                failures+=1
                                sys.stderr.write(f'FAILED n={n} {mode} seed={seed} rep={repeat} {name}: {exc}\n')
                                rows.append(dict(mode=mode,n=n,seed=seed,repeat=repeat,algorithm=name,
                                                 value='',compute_s='',wall_s='',peak_mib='',valid=False))
                        values={x['value'] for x in results.values()}
                        consistent=len(results)==3 and len(values)==1
                        for row in rows[-3:]: row['valid']=consistent
                        if not consistent:
                            failures+=1
                            print(f'MISMATCH/FAILED {mode} n={n} seed={seed}, r={[ (k,v["value"]) for k,v in results.items() ]}',flush=True)
                        else:
                            print(f'OK {mode:<9} n={n:<6} seed={seed:<5} rep={repeat} r={values.pop()} '
                                  + ' '.join(f'{x}:{results[x]["compute_s"]:.4f}s / {results[x]["peak_mib"]:.1f}MiB' for x in ('ganesh','ssp','ffk')),flush=True)
                    raw.unlink(); gin.unlink()
    with open(args.out,'w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['mode','n','seed','repeat','algorithm','value','compute_s','wall_s','peak_mib','valid'])
        writer.writeheader();writer.writerows(rows)
    print(f'CSV: {args.out}; failures/mismatches: {failures}')
    if failures: sys.exit(1)

if __name__=='__main__': main()
