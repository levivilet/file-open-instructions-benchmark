"""Diagnostic same-release local Problems summary calculation; not an official result.

These are experimental artifacts, never official-release or report inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
from compare_lazy import VerifiedCapture
import platform
import subprocess
import os

import benchmark
import compare_open
import install
import report

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / 'config/summary-patch.json').read_text())
ORIGINAL = SPEC['before']
CANDIDATE = SPEC['after']
if CANDIDATE != (ROOT / 'config/summary-expression.js').read_text().strip():
    raise ValueError('summary semantic control and frozen patch differ')


def patch_bundle(directory):
    bundle = directory / SPEC['path']
    before = bundle.read_text()
    if install.checksum(bundle) != SPEC['beforeSha256'] or before.count(ORIGINAL) != 1:
        raise ValueError('official summary bundle differs from frozen input')
    after = before.replace(ORIGINAL, CANDIDATE)
    if hashlib.sha256(after.encode()).hexdigest() != SPEC['afterSha256']:
        raise ValueError('summary candidate differs from frozen output')
    bundle.write_text(after)
    return SPEC


class SummaryCapture(VerifiedCapture):
    def finish(self):
        result = super().finish()
        result['problemsWorkerObserved'] = any('/packages/problems-view/dist/problemsViewWorkerMain.js' in target['url']
                                                for target in result['observedWorkerTargets'])
        return result


def validate_experiment(data):
    editor = json.loads((ROOT / 'config/summary-baseline.json').read_text())
    if (data.get('baseline') != editor or data.get('candidate') != editor
            or data.get('protocol') != benchmark.PROTOCOL
            or data.get('host', {}).get('machine') != 'aarch64'):
        raise ValueError('summary comparison identity, protocol or architecture mismatch')
    patch = data.get('experimentalBundlePatch', {})
    if patch != SPEC:
        raise ValueError('summary comparison differs from the intended function patch')
    pairs = data.get('pairs', [])
    if len(pairs) != 40:
        raise ValueError('summary comparison requires all forty pairs')
    for index, pair in enumerate(pairs):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        if pair.get('index') != index or pair.get('order') != order:
            raise ValueError('summary comparison must preserve alternating pair order')
    for pair in pairs:
        for variant in ['baseline', 'candidate']:
            trial = pair[variant]
            targets = trial.get('observedWorkerTargets')
            if (trial.get('observedAfterCutoff') is not True or not isinstance(targets, list)
                    or not all(isinstance(target, dict) and isinstance(target.get('url'), str) for target in targets)
                    or not any('/packages/editor-worker/dist/editorWorkerMain.js' in target['url'] for target in targets)
                    or trial.get('problemsWorkerObserved') is not any('/packages/problems-view/dist/problemsViewWorkerMain.js' in target['url'] for target in targets)):
                raise ValueError('missing or inconsistent post-cutoff summary worker observation')
    for variant in ['baseline', 'candidate']:
        report.validate_result({'editor': editor, 'host': data['host'], 'protocol': data['protocol'],
                                'trials': [pair[variant] for pair in pairs]}, 'lvce')
    if data.get('summary') != compare_open.summarize(pairs):
        raise ValueError('summary comparison summary differs from raw trials')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=40)
    args = parser.parse_args()
    if args.repeats != 40 or platform.machine() != 'aarch64':
        parser.error('requires forty pairs on ARM64')
    benchmark.Capture = SummaryCapture
    editor = json.loads((ROOT / 'config/summary-baseline.json').read_text())
    (ROOT / '.tmp/apps').mkdir(parents=True, exist_ok=True)
    archive = ROOT / '.tmp/apps' / editor['archive']
    install.download(editor, archive)
    baseline_directory = ROOT / '.tmp/apps/lvce-summary-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-summary-candidate'
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
        'design': 'same official release; status-bar summary uses existing renderer and editor RPC with unchanged filtering, deduplication and application context; panel worker remains; fresh profiles; forty alternating pairs; worker observation after cutoff only; diagnostic',
        'pairs': [],
    }
    destination = Path('results/summary-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see summary-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    validate_experiment(output)
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
