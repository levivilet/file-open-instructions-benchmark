"""Diagnostic joint runtime and worker-minification comparison; not an official result."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import os

import benchmark
import compare_open
import compare_runtime
import compare_minify
import install

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = 'official LVCE v0.123.15'
CANDIDATE = 'Electron 44.8.0 with guarded worker minification'


def patch_bundle(directory):
    # Validate the complete unchanged application before replacing its runtime.
    runtime = compare_runtime.patch_bundle(directory)
    minification = compare_minify.patch_bundle(directory)
    return {'before': ORIGINAL, 'after': CANDIDATE,
            'runtime': runtime, 'minification': minification}


def validate_experiment(data):
    patch = data.get('experimentalBundlePatch', {})
    if patch.get('before') != ORIGINAL or patch.get('after') != CANDIDATE:
        raise ValueError('combined comparison identity mismatch')
    # Reuse both full raw-trial validators, including their frozen identities.
    for key, module in [('runtime', compare_runtime), ('minification', compare_minify)]:
        module.validate_experiment(dict(data, experimentalBundlePatch=patch.get(key, {})))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=40)
    args = parser.parse_args()
    if args.repeats != 40 or platform.machine() != 'aarch64':
        parser.error('requires forty pairs on ARM64')
    editor = json.loads((ROOT / 'config/minify-baseline.json').read_text())
    (ROOT / '.tmp/apps').mkdir(parents=True, exist_ok=True)
    archive = ROOT / '.tmp/apps' / editor['archive']
    install.download(editor, archive)
    baseline_directory = ROOT / '.tmp/apps/lvce-combined-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-combined-candidate'
    install.extract(editor, archive, directory)
    patch = patch_bundle(directory)
    candidate = dict(editor, binary=str(directory / editor['binary']))
    output = {
        'baseline': editor, 'candidate': editor, 'experimentalBundlePatch': patch,
        'protocol': benchmark.PROTOCOL,
        'host': {'machine': platform.machine(), 'kernel': platform.release(),
                 'cpuinfo': Path('/proc/cpuinfo').read_text(),
                 'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
                 'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')},
        'design': 'same verified release; pinned Electron runtime replacement followed by guarded worker minification; alternating order; fresh profiles; unchanged counting protocol; diagnostic only',
        'pairs': [],
    }
    destination = Path('results/combined-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see combined-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    validate_experiment(output)
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
