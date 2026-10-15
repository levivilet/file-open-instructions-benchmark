"""Uninstrumented, alternating-order before/after LVCE file-open trials."""
import argparse
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess

import benchmark
import install

ROOT = Path(__file__).resolve().parent.parent


def summarize(pairs):
    differences = [pair['baseline']['instructions'] - pair['candidate']['instructions'] for pair in pairs]
    rng = random.Random(0)
    means = sorted(statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(10000))
    return {
        'baselineMedian': statistics.median(pair['baseline']['instructions'] for pair in pairs),
        'candidateMedian': statistics.median(pair['candidate']['instructions'] for pair in pairs),
        'meanPairedInstructionsSaved': statistics.mean(differences),
        'bootstrap95MeanSavings': [means[250], means[9749]],
        'note': 'Positive savings favor candidate; bootstrap interval is diagnostic, not a replacement for raw trial inspection.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=10, help='number of pairs')
    args = parser.parse_args()
    if args.repeats < 3 or platform.machine() != 'aarch64':
        parser.error('requires at least three pairs on ARM64')
    baseline = json.loads((ROOT / 'config/lvce-baseline.json').read_text())
    candidate = next(e for e in json.loads(install.LOCK.read_text()) if e['id'] == 'lvce')
    archive = ROOT / '.tmp/apps' / baseline['archive']
    install.download(baseline, archive)
    directory = ROOT / '.tmp/apps/lvce-baseline'
    install.extract(baseline, archive, directory)
    baseline_trial = dict(baseline, binary=str(directory / baseline['binary']))
    output = {
        'baseline': baseline, 'candidate': candidate, 'protocol': benchmark.PROTOCOL,
        'host': {'machine': platform.machine(), 'kernel': platform.release(),
                 'cpuinfo': Path('/proc/cpuinfo').read_text(),
                 'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
                 'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')},
        'design': 'fresh profile each trial; alternate baseline/candidate order per pair; unchanged open protocol; no sampling',
        'pairs': [],
    }
    destination = Path('results/comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            entry = baseline_trial if variant == 'baseline' else candidate
            pair[variant] = benchmark.trial(entry, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = summarize(output['pairs'])
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
