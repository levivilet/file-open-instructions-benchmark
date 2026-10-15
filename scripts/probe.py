"""Fail closed if the runner cannot count retired hardware instructions."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

out = Path('results')
out.mkdir(exist_ok=True)
perf = sys.argv[1]
command = [perf, 'stat', '-x', ';', '-e', 'instructions', '--',
           sys.executable, '-c', 'sum(i*i for i in range(1000000))']
result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
metadata = {
    'architecture': platform.machine(), 'kernel': platform.release(),
    'cpuinfo': Path('/proc/cpuinfo').read_text(),
    'event_sources': sorted(p.name for p in Path('/sys/bus/event_source/devices').iterdir()),
    'perf_version': subprocess.check_output([perf, '--version'], text=True).strip(),
    'perf_event_paranoid': Path('/proc/sys/kernel/perf_event_paranoid').read_text().strip(),
    'command': command, 'returncode': result.returncode,
    'stdout': result.stdout, 'stderr': result.stderr,
}
counts = []
for line in result.stderr.splitlines():
    fields = line.split(';')
    if len(fields) >= 3 and fields[2].strip() == 'instructions':
        try:
            counts.append(int(fields[0].strip()))
        except ValueError:
            pass
metadata['valid'] = result.returncode == 0 and len(counts) == 1 and counts[0] > 0
metadata['instructions'] = counts[0] if metadata['valid'] else None
(out / 'feasibility.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(result.stderr)
print(json.dumps({k: metadata[k] for k in ['architecture', 'event_sources', 'valid', 'instructions']}))
if not metadata['valid']:
    sys.exit('Runner does not expose a usable retired-instruction counter; no benchmark may be published.')
