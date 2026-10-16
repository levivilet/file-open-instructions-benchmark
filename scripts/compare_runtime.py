"""Diagnostic same-release native Electron runtime configuration; not an official result.

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
ORIGINAL = 'official LVCE v0.123.15 Electron 44.7.0 runtime'
CANDIDATE = 'same application files; official Electron 44.8.0 runtime'


def tree_hash(root):
    import os
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            value = os.readlink(path).encode()
        elif path.is_file():
            value = hashlib.sha256(path.read_bytes()).digest()
        else:
            continue
        digest.update(str(path.relative_to(root)).encode() + b'\0' + value + b'\0')
    return digest.hexdigest()


def patch_bundle(directory):
    import shutil
    import tempfile
    runtime = json.loads((ROOT / 'config/runtime-candidate.json').read_text())
    archive = ROOT / '.tmp/apps' / runtime['archive']
    install.download(runtime, archive)
    destination = directory / 'usr/lib/lvce'
    before_version = (destination / 'version').read_text().strip()
    if before_version != '44.7.0':
        raise ValueError('unexpected baseline runtime')
    app_hash = tree_hash(destination / 'resources/app')
    if app_hash != json.loads((ROOT / 'config/runtime-application.json').read_text())['sha256']:
        raise ValueError('baseline application differs from guarded release')
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        saved_app = Path(temporary) / 'app'
        shutil.copytree(destination / 'resources/app', saved_app, symlinks=True)
        install.extract(runtime, archive, destination)
        (destination / 'electron').rename(destination / 'lvce')
        shutil.copytree(saved_app, destination / 'resources/app', symlinks=True)
    if (destination / 'version').read_text().strip() != runtime['version'] or tree_hash(destination / 'resources/app') != app_hash:
        raise ValueError('runtime replacement changed the application or version identity')
    return {'before': ORIGINAL, 'after': CANDIDATE, 'runtime': runtime, 'applicationSha256': app_hash,
            'status': 'diagnostic runtime replacement; original application/config bytes retained'}



def validate_experiment(data):
    editor = json.loads((ROOT / 'config/runtime-baseline.json').read_text())
    if (data.get('baseline') != editor or data.get('candidate') != editor
            or data.get('protocol') != benchmark.PROTOCOL
            or data.get('host', {}).get('machine') != 'aarch64'):
        raise ValueError('runtime comparison identity, protocol or architecture mismatch')
    patch = data.get('experimentalBundlePatch', {})
    if patch.get('before') != ORIGINAL or patch.get('after') != CANDIDATE:
        raise ValueError('runtime comparison differs from the intended function patch')
    if patch.get('runtime') != json.loads((ROOT / 'config/runtime-candidate.json').read_text()):
        raise ValueError('runtime identity mismatch')
    if patch.get('applicationSha256') != json.loads((ROOT / 'config/runtime-application.json').read_text())['sha256']:
        raise ValueError('application identity mismatch')
    pairs = data.get('pairs', [])
    if len(pairs) != 40:
        raise ValueError('runtime comparison requires all forty pairs')
    for index, pair in enumerate(pairs):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        if pair.get('index') != index or pair.get('order') != order:
            raise ValueError('runtime comparison must preserve alternating pair order')
    for variant in ['baseline', 'candidate']:
        report.validate_result({'editor': editor, 'host': data['host'], 'protocol': data['protocol'],
                                'trials': [pair[variant] for pair in pairs]}, 'lvce')
    if data.get('summary') != compare_open.summarize(pairs):
        raise ValueError('runtime comparison summary differs from raw trials')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=40)
    args = parser.parse_args()
    if args.repeats != 40 or platform.machine() != 'aarch64':
        parser.error('requires forty pairs on ARM64')
    editor = json.loads((ROOT / 'config/runtime-baseline.json').read_text())
    (ROOT / '.tmp/apps').mkdir(parents=True, exist_ok=True)
    archive = ROOT / '.tmp/apps' / editor['archive']
    install.download(editor, archive)
    baseline_directory = ROOT / '.tmp/apps/lvce-runtime-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-runtime-candidate'
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
        'design': 'same verified LVCE application files; only official Electron runtime differs; alternating order; fresh profiles; unchanged counting protocol; diagnostic only',
        'pairs': [],
    }
    destination = Path('results/runtime-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see runtime-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    validate_experiment(output)
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
