#!/usr/bin/env python3
"""Local, bounded build runner. No sudo, network, automatic retry or unbounded fallback."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

MIB = 1024 * 1024

def resources():
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        mem[key] = int(value.strip().split()[0]) // 1024
    available = mem['MemAvailable']
    # Respect every ancestor's remaining cgroup-v2 budget, not only the root.
    group = next((l.split(':', 2)[2] for l in Path('/proc/self/cgroup').read_text().splitlines() if l.startswith('0::')), '/')
    root = Path('/sys/fs/cgroup')
    current = root / group.lstrip('/')
    while current == root or root in current.parents:
        try:
            limit = (current / 'memory.max').read_text().strip()
            if limit != 'max':
                used = int((current / 'memory.current').read_text())
                available = min(available, max(0, int(limit) - used) // MIB)
        except (OSError, ValueError):
            pass
        if current == root:
            break
        current = current.parent
    pressure = 0.0
    try:
        full = next(l for l in Path('/proc/pressure/memory').read_text().splitlines() if l.startswith('full '))
        pressure = float(dict(x.split('=') for x in full.split()[1:])['avg10'])
    except (OSError, ValueError, StopIteration, KeyError):
        pass
    return dict(total_mib=mem['MemTotal'], available_mib=available,
                disk_free_mib=shutil.disk_usage(Path.cwd()).free // MIB,
                pressure_full_avg10=pressure)

def plan(r):
    reserve = max(2048, r['total_mib'] // 4)
    maximum = min(4096, max(0, r['available_mib'] - reserve))
    heap = min(3072, max(0, maximum - max(512, maximum // 5)))
    reasons = []
    if heap < 1536:
        reasons.append('Insufficient headroom for compilation; defer or build on another device.')
    if r['disk_free_mib'] < 4096:
        reasons.append('Less than 4 GiB free in the build filesystem.')
    if r['pressure_full_avg10'] >= 5:
        reasons.append('Memory pressure is already elevated.')
    return dict(observed_at=datetime.now(timezone.utc).isoformat(), observed=r,
                ready=not reasons, reasons=reasons, reserve_mib=reserve,
                memory_max_mib=maximum, memory_high_mib=max(0, maximum - 128),
                node_heap_mib=heap, workers=1, cpu_quota_percent=100,
                note='Planning budget, not an allocation. The run remeasures and enforces a whole-process-tree cgroup limit.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--log', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not args.run:
        print(json.dumps(plan(resources()), indent=2))
        return 0
    if not command:
        parser.error('--run requires -- command arguments')
    # A per-user lock covers every wrapper invocation on this device.
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
    if not runtime.is_dir():
        sys.exit('No user runtime directory; refusing an unprotected build.')
    with (runtime / 'isle-build.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('Another bounded build is running; try after it finishes.')
        budget = plan(resources())
        print(json.dumps(budget), flush=True)
        if not budget['ready']:
            return 2
        unit = 'isle-build-' + uuid.uuid4().hex
        props = [
            'MemoryAccounting=yes', 'LimitCORE=0', 'MemoryMax=' + str(budget['memory_max_mib']) + 'M',
            'MemoryHigh=' + str(budget['memory_high_mib']) + 'M', 'MemorySwapMax=0',
            'CPUQuota=100%', 'CPUWeight=10', 'Nice=15',
            'IOSchedulingClass=idle', 'OOMPolicy=stop', 'KillMode=control-group',
            'TimeoutStopSec=10s',
        ]
        env = dict(os.environ)
        # Ignore inherited Node options that could defeat the selected heap.
        env['NODE_OPTIONS'] = '--max-old-space-size=' + str(budget['node_heap_mib'])
        env['NG_BUILD_MAX_WORKERS'] = '1'
        env['CMAKE_BUILD_PARALLEL_LEVEL'] = '1'
        env['MAKEFLAGS'] = '-j1'
        env['DPKG_DEB_THREADS_MAX'] = '1'
        env['DPKG_DEB_COMPRESSOR_TYPE'] = 'zstd'
        env['DPKG_DEB_COMPRESSOR_LEVEL'] = '3'
        cmd = ['systemd-run', '--user', '--wait', '--pipe', '--unit=' + unit,
               '--working-directory=' + str(Path.cwd())]
        for p in props:
            cmd += ['--property=' + p]
        for k in ['PATH', 'NODE_OPTIONS', 'NG_BUILD_MAX_WORKERS', 'CMAKE_BUILD_PARALLEL_LEVEL', 'MAKEFLAGS', 'DPKG_DEB_THREADS_MAX', 'DPKG_DEB_COMPRESSOR_TYPE', 'DPKG_DEB_COMPRESSOR_LEVEL']:
            cmd += ['--setenv=' + k + '=' + env[k]]
        cmd += ['--'] + command
        outcome = dict(plan=budget, unit=unit, command=command)
        process = None
        try:
            process = subprocess.Popen(cmd, env=env)
            low = 0
            cgroup = None
            outcome["peak_memory_bytes"] = 0
            while process.poll() is None:
                time.sleep(2)
                if cgroup is None:
                    group = subprocess.run(['systemctl','--user','show',unit,'-p','ControlGroup','--value'],
                                           capture_output=True,text=True).stdout.strip()
                    if group:
                        cgroup = Path('/sys/fs/cgroup') / group.lstrip('/')
                if cgroup:
                    try:
                        outcome['peak_memory_bytes'] = max(outcome['peak_memory_bytes'],
                                                           int((cgroup/'memory.peak').read_text()))
                    except (OSError, ValueError):
                        pass
                r = resources()
                low = low + 1 if r['available_mib'] < 768 or r['pressure_full_avg10'] >= 20 else 0
                if low >= 3:
                    outcome['stopped_reason'] = 'Sustained host memory pressure'
                    subprocess.run(['systemctl','--user','stop',unit], check=False)
                    break
            code = process.wait()
            if outcome.get('stopped_reason'):
                code = 75
            outcome['exit_code'] = code
            return code
        finally:
            outcome['service'] = subprocess.run(
                ['systemctl','--user','show',unit,'-p','MemoryPeak','-p','Result','-p','ExecMainStatus'],
                capture_output=True,text=True).stdout
            subprocess.run(['systemctl','--user','stop',unit], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['systemctl','--user','reset-failed',unit], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            outcome['finished_at'] = datetime.now(timezone.utc).isoformat()
            if args.log:
                args.log.write_text(json.dumps(outcome, indent=2) + '\n')
            print(json.dumps(outcome), flush=True)

if __name__ == '__main__':
    sys.exit(main())
