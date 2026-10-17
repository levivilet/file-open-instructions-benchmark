"""Diagnostic same-release native worker bundle minification configuration; not an official result.

These are experimental artifacts, never official-release or report inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import os

import benchmark
import compare_open
import install
import report

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = 'checksum-pinned official worker bundles'
CANDIDATE = 'esbuild@0.28.2 --minify --keep-names --format=esm'


def patch_bundle(directory):
    expected = json.loads((ROOT / 'config/minify-inputs.json').read_text())
    paths = sorted(directory.glob('usr/lib/lvce/resources/app/static/*/packages/*/dist/*WorkerMain.js'))
    actual = [{'path': str(path.relative_to(directory)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    if actual != expected:
        raise ValueError('official worker inputs differ from guarded minification manifest')
    before_bytes = sum(path.stat().st_size for path in paths)
    subprocess.run(['npx', '--yes', 'esbuild@0.28.2', *map(str, paths), '--minify', '--keep-names', '--format=esm',
                    '--outbase=' + str(directory), '--outdir=' + str(directory), '--allow-overwrite', '--log-level=warning'], check=True)
    after = [{'path': str(path.relative_to(directory)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size} for path in paths]
    return {'before': ORIGINAL, 'after': CANDIDATE, 'inputs': actual, 'outputs': after,
            'beforeBytes': before_bytes, 'afterBytes': sum(item['bytes'] for item in after),
            'status': 'diagnostic same-release transformation; no production change yet'}



def validate_experiment(data):
    editor = json.loads((ROOT / 'config/minify-baseline.json').read_text())
    if (data.get('baseline') != editor or data.get('candidate') != editor
            or data.get('protocol') != benchmark.PROTOCOL
            or data.get('host', {}).get('machine') != 'aarch64'):
        raise ValueError('minification comparison identity, protocol or architecture mismatch')
    patch = data.get('experimentalBundlePatch', {})
    if patch.get('before') != ORIGINAL or patch.get('after') != CANDIDATE:
        raise ValueError('minification comparison differs from the intended function patch')
    if patch.get('inputs') != json.loads((ROOT / 'config/minify-inputs.json').read_text()):
        raise ValueError('minification input identity mismatch')
    if patch.get('outputs') != json.loads((ROOT / 'config/minify-outputs.json').read_text()):
        raise ValueError('minified output identity mismatch')
    pairs = data.get('pairs', [])
    if len(pairs) != 40:
        raise ValueError('minification comparison requires all forty pairs')
    for index, pair in enumerate(pairs):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        if pair.get('index') != index or pair.get('order') != order:
            raise ValueError('minification comparison must preserve alternating pair order')
    for variant in ['baseline', 'candidate']:
        report.validate_result({'editor': editor, 'host': data['host'], 'protocol': data['protocol'],
                                'trials': [pair[variant] for pair in pairs]}, 'lvce')
    if data.get('summary') != compare_open.summarize(pairs):
        raise ValueError('minification comparison summary differs from raw trials')
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
    baseline_directory = ROOT / '.tmp/apps/lvce-minify-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-minify-candidate'
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
        'design': 'same verified release; only recorded editor bundle function differs; alternating order; fresh profiles; no sampling; diagnostic only',
        'pairs': [],
    }
    destination = Path('results/minify-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see minify-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    validate_experiment(output)
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
