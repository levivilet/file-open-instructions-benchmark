"""Diagnostic paired comparison of one bundle change in the same official LVCE.

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

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = '''const toLowerCaseMap = map => {
  return Object.fromEntries(Object.entries(map).map(entry => [entry[0].toLowerCase(), entry[1]]));
};'''
CANDIDATE = '''const lowerCaseMaps = new WeakMap();
const toLowerCaseMap = map => {
  const cached = lowerCaseMaps.get(map);
  if (cached) {
    return cached;
  }
  const result = Object.create(null);
  for (const key of Object.keys(map)) {
    result[key.toLowerCase()] = map[key];
  }
  lowerCaseMaps.set(map, result);
  return result;
};'''


def patch_bundle(directory):
    matches = list(directory.glob('usr/lib/lvce/resources/app/static/*/packages/icon-theme-worker/dist/iconThemeWorkerMain.js'))
    if len(matches) != 1:
        raise ValueError('expected exactly one installed icon bundle')
    bundle = matches[0]
    before = bundle.read_text()
    if before.count(ORIGINAL) != 1:
        raise ValueError('official bundle differs from expected baseline; do not apply speculative patch')
    after = before.replace(ORIGINAL, CANDIDATE)
    bundle.write_text(after)
    return {'path': str(bundle.relative_to(directory)),
            'beforeSha256': hashlib.sha256(before.encode()).hexdigest(),
            'afterSha256': hashlib.sha256(after.encode()).hexdigest(),
            'before': ORIGINAL, 'after': CANDIDATE,
            'producerPr': 'https://github.com/lvce-editor/icon-theme-worker/pull/180',
            'producerHead': '19f4afb65854f5458cfc407d69b9c21aa9f36684'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    if args.repeats < 3 or platform.machine() != 'aarch64':
        parser.error('requires at least three pairs on ARM64')
    editor = json.loads((ROOT / 'config/lvce-baseline.json').read_text())
    archive = ROOT / '.tmp/apps' / editor['archive']
    install.download(editor, archive)
    baseline_directory = ROOT / '.tmp/apps/lvce-icon-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-icon-candidate'
    install.extract(editor, archive, directory)
    patch = patch_bundle(directory)
    candidate = dict(editor, binary=str(directory / editor['binary']))
    output = {
        'baseline': editor, 'candidate': candidate, 'experimentalBundlePatch': patch,
        'protocol': benchmark.PROTOCOL,
        'host': {'machine': platform.machine(), 'kernel': platform.release(),
                 'cpuinfo': Path('/proc/cpuinfo').read_text(),
                 'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
                 'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')},
        'design': 'same verified release; only recorded icon bundle function differs; alternating order; fresh profiles; no sampling; diagnostic only',
        'pairs': [],
    }
    destination = Path('results/icon-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see icon-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
