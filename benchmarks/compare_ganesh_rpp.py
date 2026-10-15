#!/usr/bin/env python3
"""Compare unchanged RPP-SSP with the Ganesh baseline.

Identical preference profile per trial. Larger instances are memory-capped
rather to protect the GitHub runner. Results are flushed after every trial.
"""
import argparse
import csv
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import time

PARSER = {
    'ssp': re.compile(r'OK \(SSP\+Dijkstra\).*?min_cost\s*=\s*(\d+)', re.S),
    'ganesh': re.compile(r'^\s*(\d+)\s*$', re.M),
}
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
INTERNAL_TIME = re.compile(r'Total program time:\s*(' + NUMBER + r')\s*s\.')
GANESH_TIME = re.compile(r'total_ms=(' + NUMBER + r')')
FIELDS = ('mode', 'n', 'seed', 'repeat', 'algorithm', 'value', 'compute_s',
          'wall_s', 'peak_mib', 'status', 'reason', 'agreement', 'valid')


def generate(n, mode, seed, path):
    rng = random.Random(seed)
    with path.open('w', buffering=1024*1024) as f:
        for side in range(2):
            for p in range(n):
                if mode == 'cyclic':
                    row = ((p + (1 if side else 0) + j) % n for j in range(n))
                elif mode == 'identical':
                    row = range(n)
                elif mode == 'random':
                    arr = list(range(n))
                    rng.shuffle(arr)
                    row = arr
                else:
                    raise ValueError(mode)
                f.write(' '.join(map(str, row)) + '\n')


def timed_run(name, args, cwd, stdin_path, timeout, vmem_mib=None):
    with tempfile.NamedTemporaryFile(prefix='rpp_rss_', delete=False) as rf:
        rss_path = rf.name
    command = ['/usr/bin/time', '-f', '%M', '-o', rss_path] + list(map(str, args))

    def memory_limit():
        if vmem_mib is not None:
            import resource
            cap = int(vmem_mib) * (1 << 20)
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

    try:
        with (open(stdin_path, 'rb') if stdin_path else open(os.devnull, 'rb')) as fd:
            started = time.perf_counter()
            cp = subprocess.run(command, cwd=cwd, stdin=fd, capture_output=True,
                                text=True, timeout=timeout, check=False,
                                preexec_fn=memory_limit if vmem_mib is not None else None)
            wall = time.perf_counter() - started
        rss_text = pathlib.Path(rss_path).read_text().strip()
        if cp.returncode:
            raise RuntimeError(f'{name}: exit {cp.returncode}; stdout={cp.stdout[-500:]!r}; '
                               f'stderr={cp.stderr[-500:]!r}')
        m = PARSER[name].search(cp.stdout)
        if not m:
            raise RuntimeError(f'{name}: missing optimum; stdout={cp.stdout[-700:]!r}')
        value = int(m.group(1))
        if name == 'ganesh':
            tm = GANESH_TIME.search(cp.stderr)
            if tm is None:
                raise RuntimeError(f'Ganesh timing missing: {cp.stderr[-700:]}')
            internal = float(tm.group(1)) / 1000
        else:
            tm = INTERNAL_TIME.search(cp.stdout)
            if tm is None:
                raise RuntimeError(f'{name}: internal timing missing: {cp.stdout[-700:]!r}')
            internal = float(tm.group(1))
        rss = int(rss_text.splitlines()[0]) if rss_text else 0
        return dict(value=value, compute_s=internal, wall_s=wall, peak_mib=rss / 1024)
    finally:
        pathlib.Path(rss_path).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=pathlib.Path)
    p.add_argument('--ganesh', required=True, type=pathlib.Path)
    p.add_argument('--out', default='rpp_ganesh_two_way.csv')
    p.add_argument('--sizes', default='1000,3000,5000,10000')
    p.add_argument('--modes', default='random,cyclic,identical')
    p.add_argument('--seeds', default='41,42,43')
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--max-vmem-mib', type=int, default=5120,
                   help='Per-process virtual-memory cap (MiB)')
    args = p.parse_args()
    if args.repeats < 1: p.error('--repeats must be positive')
    repo = args.repo.resolve()
    executables = {'ganesh': args.ganesh.resolve(), 'ssp': repo / 'ssp_dijkstra'}
    for name, binary in executables.items():
        if not binary.is_file() or not os.access(binary, os.X_OK):
            p.error(f'Missing executable {name}: {binary}')
    sizes = [int(x.strip()) for x in args.sizes.split(',')]
    modes = [x.strip() for x in args.modes.split(',')]
    seeds = [int(x.strip()) for x in args.seeds.split(',')]
    if any(n < 1 for n in sizes) or any(m not in ('random','cyclic','identical') for m in modes):
        p.error('Invalid sizes or profile modes')
    failures = completed = 0
    output = pathlib.Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f'CONFIG sizes={sizes} modes={modes} repeats={args.repeats} '
          f'max-vmem-mib={args.max_vmem_mib}', flush=True)

    with output.open('w', newline='') as outf, tempfile.TemporaryDirectory(prefix='rpp_comparison_') as dirname:
        csvout = csv.DictWriter(outf, fieldnames=FIELDS)
        csvout.writeheader()
        outf.flush()
        td = pathlib.Path(dirname)
        for n in sizes:
            for mode in modes:
                for seed in (seeds if mode == 'random' else [0]):
                    raw = td / f'{mode}_{n}_{seed}.txt'
                    gin = td / f'{mode}_{n}_{seed}_ganesh.txt'
                    generate(n, mode, seed, raw)
                    with gin.open('wb') as f, raw.open('rb') as src:
                        f.write((str(n) + '\n').encode())
                        while block := src.read(1 << 20): f.write(block)
                    for repeat in range(args.repeats):
                        results = {}
                        records = []
                        for name in ('ganesh', 'ssp'):
                            rec = dict(mode=mode, n=n, seed=seed, repeat=repeat, algorithm=name,
                                       value='', compute_s='', wall_s='', peak_mib='',
                                       status='failed', reason='', agreement='', valid=False)
                            if name == 'ganesh':
                                command = [str(executables[name]), '--timings', '--skip-check']
                                input_path = gin
                            else:
                                command = [str(executables[name]), str(raw), str(n), '1']
                                input_path = None
                            try:
                                measurement = timed_run(name, command, td, input_path,
                                                        args.timeout, args.max_vmem_mib)
                                rec.update(measurement, status='ok')
                                results[name] = measurement
                            except Exception as exc:
                                failures += 1
                                rec['reason'] = str(exc)[:500]
                                print(f'FAILED mode={mode} n={n} seed={seed} rep={repeat} '
                                      f'algorithm={name}: {exc}', file=sys.stderr, flush=True)
                            records.append(rec)
                        agreement = len(results) >= 2 and len({r['value'] for r in results.values()}) == 1
                        valid = len(results) == 2 and agreement
                        for rec in records:
                            rec.update(agreement=agreement, valid=valid)
                            csvout.writerow(rec)
                        outf.flush()  # Retain every completed trial if runner stops later.
                        if not agreement:
                            failures += 1
                            print(f'MISMATCH/FAILED mode={mode} n={n} seed={seed} rep={repeat} '
                                  f'values={[(k, r["value"]) for k,r in results.items()]}', flush=True)
                        else:
                            completed += 1
                            print(f'OK TWO-WAY mode={mode} n={n} seed={seed} rep={repeat} '
                                  f'r={next(iter(results.values()))["value"]} '
                                  + ' '.join(f'{k}:{r["compute_s"]:.6g}s/{r["peak_mib"]:.1f}MiB'
                                             for k,r in results.items()), flush=True)
                    raw.unlink()
                    gin.unlink()
    print(f'CSV: {output}; completed two-way trials={completed}, errors/mismatches={failures}', flush=True)
    if failures: sys.exit(1)

if __name__ == '__main__':
    main()
