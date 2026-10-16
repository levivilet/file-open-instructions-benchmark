"""Diagnostic rebuilt monolithic versus lazy-command editor worker comparison; not an official result.

These are experimental artifacts, never official-release or report inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import os
import shutil
import tarfile
from counters import Capture
from cdp import Page

import benchmark
import compare_open
import install
import report

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = 'rebuilt editor-worker 19.74.7 monolithic bundle; esbuild minified with names preserved'
CANDIDATE = 'same rebuilt editor-worker; optional implementations load within existing wrappers; widget registration and command ordering preserved'


def build_sources():
    recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
    patch = ROOT / 'config/lazy-source.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != recipe['sourcePatchSha256']:
        raise ValueError('lazy source patch identity mismatch')
    if subprocess.check_output(['node', '--version'], text=True).strip() != recipe['node']:
        raise ValueError('lazy build requires the pinned Node version')
    archive = ROOT / '.tmp/apps' / recipe['archive']['archive']
    archive.parent.mkdir(parents=True, exist_ok=True)
    install.download(recipe['archive'], archive)
    directory = ROOT / '.tmp/lazy-source'
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(directory, filter='data')
    roots = list(directory.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError('unexpected source archive layout')
    source = roots[0]
    if hashlib.sha256((source / 'package-lock.json').read_bytes()).hexdigest() != recipe['lockSha256']:
        raise ValueError('lazy build lockfile identity mismatch')
    subprocess.run(['nice', 'npm', 'ci'], cwd=source, check=True)
    outputs = {}
    for variant in ['baseline', 'candidate']:
        if variant == 'candidate':
            git_env = {**os.environ, 'GIT_CEILING_DIRECTORIES': str(source.parent)}
            subprocess.run(['git', 'apply', '--unidiff-zero', '--check', str(patch)], cwd=source, env=git_env, check=True)
            subprocess.run(['git', 'apply', '--unidiff-zero', str(patch)], cwd=source, env=git_env, check=True)
        built = source / '.tmp/dist/dist'
        if built.exists():
            shutil.rmtree(built)
        subprocess.run(['node', '--input-type=module', '-e',
                        "const {bundleJs} = await import('./packages/build/src/bundleJs.ts'); await bundleJs()"],
                       cwd=source, check=True)
        destination = ROOT / '.tmp' / ('lazy-built-' + variant)
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        for path in built.glob('*.js'):
            shutil.copy2(path, destination / path.name)
        paths = sorted(destination.glob('*.js'))
        subprocess.run(['npx', '--yes', 'esbuild@0.28.2', *map(str, paths), '--minify', '--keep-names', '--format=esm',
                        '--outdir=' + str(destination), '--allow-overwrite', '--log-level=warning'], check=True)
        actual = [{'path': path.name, 'sha256': install.checksum(path), 'bytes': path.stat().st_size} for path in paths]
        if actual != recipe['outputs'][variant]:
            raise ValueError('rebuilt lazy variant differs from frozen outputs: ' + variant + ': ' + json.dumps(actual))
        outputs[variant] = destination
    return outputs


def prepared_outputs():
    recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
    outputs = {}
    for variant in ['baseline', 'candidate']:
        directory = ROOT / '.tmp' / ('lazy-built-' + variant)
        actual = [{'path': path.name, 'sha256': install.checksum(path), 'bytes': path.stat().st_size}
                  for path in sorted(directory.glob('*.js'))]
        if actual != recipe['outputs'][variant]:
            raise ValueError('missing or changed prepared lazy worker outputs: ' + variant)
        outputs[variant] = directory
    return outputs


def patch_bundle(directory, variant, outputs):
    recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
    bundle = directory / recipe['workerPath']
    if install.checksum(bundle) != recipe['officialInputSha256']:
        raise ValueError('official editor worker input differs')
    for entry in recipe['outputs'][variant]:
        source = outputs[variant] / entry['path']
        if install.checksum(source) != entry['sha256']:
            raise ValueError('lazy build output changed before installation')
        destination = bundle.parent / entry['path']
        if destination.exists() and destination != bundle:
            raise ValueError('unexpected existing worker chunk')
        shutil.copy2(source, destination)
    return {'variant': variant, 'recipe': recipe}


class VerifiedCapture(Capture):
    def launch(self, command, *args, **kwargs):
        self.debug_port = int(next(arg.split('=', 1)[1] for arg in command if arg.startswith('--remote-debugging-port=')))
        return super().launch(command, *args, **kwargs)

    def finish(self):
        result = super().finish()
        if not result['cutoffAcknowledged']:
            raise ValueError('worker observation must follow acknowledged cutoff')
        page = Page(self.debug_port, timeout=5)
        try:
            targets = page.call('Target.getTargets')['targetInfos']
            observations = []
            matched = None
            destination = Path('results/lazy-worker-observation.json')
            destination.parent.mkdir(parents=True, exist_ok=True)

            def save_observations():
                destination.write_text(json.dumps({'targets': targets, 'observations': observations}, indent=2) + '\n')

            save_observations()
            for worker in targets:
                if worker['type'] != 'worker':
                    continue
                session = None
                scripts = []
                observation = {'target': worker, 'scripts': scripts}
                observations.append(observation)
                save_observations()

                def observe(method, params=None):
                    page.counter += 1
                    request_id = page.counter
                    page.socket.send(json.dumps({'id': request_id, 'sessionId': session,
                                                 'method': method, 'params': params or {}}))
                    while True:
                        message = json.loads(page.socket.recv())
                        if message.get('sessionId') == session and message.get('method') == 'Debugger.scriptParsed':
                            scripts.append(message['params']['url'])
                        if message.get('id') == request_id:
                            if 'error' in message or message.get('result', {}).get('exceptionDetails'):
                                raise RuntimeError(message)
                            return message.get('result', {})

                try:
                    session = page.call('Target.attachToTarget', targetId=worker['targetId'], flatten=True)['sessionId']
                    observe('Debugger.enable')
                    observe('Runtime.evaluate', {'expression': '0', 'returnByValue': True})
                    observe('Debugger.disable')
                except Exception as error:
                    # A pending or exiting unrelated worker may never answer.
                    # Its absence cannot establish that our cold module was absent.
                    observation['error'] = f'{type(error).__name__}: {error}'
                finally:
                    if session is not None:
                        try:
                            page.call('Target.detachFromTarget', sessionId=session)
                        except Exception as error:
                            observation['detachError'] = f'{type(error).__name__}: {error}'
                    save_observations()
                if 'error' not in observation and any('/packages/editor-worker/dist/editorWorkerMain' in url for url in scripts):
                    matched = observation
                    break
            if matched is None:
                raise ValueError('post-cutoff observation did not find editor worker scripts; see lazy-worker-observation.json')
            result['editorWorkerScripts'] = matched['scripts']
            result['editorWorkerTarget'] = matched['target']
            result['coldModuleParsed'] = any('/ColdHandlers-' in name for name in matched['scripts'])
            result['observedAfterCutoff'] = True
        finally:
            page.close()
        return result


def validate_experiment(data):
    editor = json.loads((ROOT / 'config/lazy-baseline.json').read_text())
    if (data.get('baseline') != editor or data.get('candidate') != editor
            or data.get('protocol') != benchmark.PROTOCOL
            or data.get('host', {}).get('machine') != 'aarch64'):
        raise ValueError('lazy-command comparison identity, protocol or architecture mismatch')
    recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
    for variant, key in [('baseline', 'baselineBundlePatch'), ('candidate', 'experimentalBundlePatch')]:
        if data.get(key) != {'variant': variant, 'recipe': recipe}:
            raise ValueError('lazy comparison differs from both frozen source/build transformations')
    pairs = data.get('pairs', [])
    if len(pairs) != 40:
        raise ValueError('lazy-command comparison requires all forty pairs')
    for index, pair in enumerate(pairs):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        if pair.get('index') != index or pair.get('order') != order:
            raise ValueError('lazy-command comparison must preserve alternating pair order')
    for pair in pairs:
        for variant in ['baseline', 'candidate']:
            trial = pair[variant]
            resources = trial.get('editorWorkerScripts')
            if (trial.get('observedAfterCutoff') is not True or not isinstance(resources, list)
                    or not all(isinstance(name, str) for name in resources)
                    or not any('/packages/editor-worker/dist/editorWorkerMain' in name for name in resources)
                    or trial.get('coldModuleParsed') is not any('/ColdHandlers-' in name for name in resources)):
                raise ValueError('missing or inconsistent post-cutoff worker module observation')
    for variant in ['baseline', 'candidate']:
        report.validate_result({'editor': editor, 'host': data['host'], 'protocol': data['protocol'],
                                'trials': [pair[variant] for pair in pairs]}, 'lvce')
    if data.get('summary') != compare_open.summarize(pairs):
        raise ValueError('lazy-command comparison summary differs from raw trials')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce'], required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=40)
    args = parser.parse_args()
    if args.repeats != 40 or platform.machine() != 'aarch64':
        parser.error('requires forty pairs on ARM64')
    outputs = prepared_outputs()
    benchmark.Capture = VerifiedCapture
    editor = json.loads((ROOT / 'config/lazy-baseline.json').read_text())
    (ROOT / '.tmp/apps').mkdir(parents=True, exist_ok=True)
    archive = ROOT / '.tmp/apps' / editor['archive']
    install.download(editor, archive)
    baseline_directory = ROOT / '.tmp/apps/lvce-lazy-baseline'
    install.extract(editor, archive, baseline_directory)
    baseline_patch = patch_bundle(baseline_directory, 'baseline', outputs)
    baseline = dict(editor, binary=str(baseline_directory / editor['binary']))
    directory = ROOT / '.tmp/apps/lvce-lazy-candidate'
    install.extract(editor, archive, directory)
    patch = patch_bundle(directory, 'candidate', outputs)
    candidate = dict(editor, binary=str(directory / editor['binary']))
    output = {
        'baseline': editor, 'candidate': editor, 'experimentalBundlePatch': patch, 'baselineBundlePatch': baseline_patch,
        'protocol': benchmark.PROTOCOL,
        'host': {'machine': platform.machine(), 'kernel': platform.release(),
                 'cpuinfo': Path('/proc/cpuinfo').read_text(),
                 'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
                 'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')},
        'design': 'same official host release with same rebuilt editor19.74.7 on both sides; monolithic versus demand-loaded command implementations inside existing wrappers; all chunks minified identically; alternating fresh profiles; no sampling; module observation only after acknowledged cutoff; diagnostic only',
        'pairs': [],
    }
    destination = Path('results/lazy-comparison.json')
    destination.parent.mkdir(exist_ok=True)
    for index in range(args.repeats):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        pair = {'index': index, 'order': order}
        output['pairs'].append(pair)
        for variant in order:
            pair[variant] = benchmark.trial(baseline if variant == 'baseline' else candidate, args.perf)
            destination.write_text(json.dumps(output, indent=2) + '\n')
            if not pair[variant]['valid']:
                raise SystemExit(f'invalid {variant} trial; see lazy-comparison.json')
            print(f"pair {index + 1} {variant}: {pair[variant]['instructions']}", flush=True)
    output['summary'] = compare_open.summarize(output['pairs'])
    validate_experiment(output)
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output['summary']), flush=True)


if __name__ == '__main__':
    main()
