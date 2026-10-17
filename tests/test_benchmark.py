import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark import CONTENT, PROTOCOL, RESPONSE_WINDOW_SECONDS, command_for, is_main_window
from counters import parse_counter
import report
import benchmark
import install
import compare_icon
import compare_ipc
import compare_minify
import compare_runtime
import compare_combined
import compare_font
import compare_render
import compare_plain
import compare_cache
import compare_compact
import compare_lazy
import compare_summary


def trial(count=100):
    return {'valid': True, 'instructions': count, 'measurementSeconds': 1.02,
            'openDispatchSeconds': .02, 'openToCutoffSeconds': 1.0,
            'source': 'perf stat instructions, per-CPU cgroup',
            'documentLoaded': True, 'documentContent': CONTENT,
            'documentPath': '/tmp/trial/hello.txt', 'fixture': '/tmp/trial/hello.txt',
            'windowTitle': 'hello.txt - LVCE Editor', 'focusPid': 12,
            'pidsAtOpen': [12, 14], 'readiness': {'windowId': '1'},
            'cutoffAcknowledged': True,
            'rawCounter': f'{count};;instructions;open-test;1234;100.00;;\n'}


def result(editor, counts=(100, 300, 200)):
    trials = [dict(trial(count), repeat=index + 1) for index, count in enumerate(counts)]
    if editor['id'] in {'vscode', 'geany'}:
        for item in trials:
            item['windowTitle'] = 'hello.txt - ' + editor['name']
    host = {'machine': 'aarch64'}
    if editor.get('package'):
        host['packageVersion'] = editor['version']
    return {'editor': editor, 'protocol': PROTOCOL, 'host': host, 'trials': trials}


class BenchmarkTest(unittest.TestCase):
    def test_protocol_describes_measured_open_and_exact_fixture(self):
        self.assertEqual(CONTENT, 'hello world')
        self.assertEqual(RESPONSE_WINDOW_SECONDS, 1.0)
        self.assertIn('Ctrl+O', PROTOCOL['action'])
        self.assertIn('File > Open File', PROTOCOL['action'])
        self.assertIn('clipboard', PROTOCOL['excluded'])

    def test_main_window_type_defaults_and_rejections(self):
        mapped = 'WM_STATE(WM_STATE):\n    window state: Normal\n'
        missing = '_NET_WM_WINDOW_TYPE:  not found.\nWM_TRANSIENT_FOR:  not found.\n'
        self.assertTrue(is_main_window(mapped + missing))
        self.assertTrue(is_main_window(mapped + '_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_NORMAL'))
        self.assertFalse(is_main_window(missing))
        for kind in ['SPLASH', 'DIALOG', 'UTILITY', 'MENU']:
            self.assertFalse(is_main_window(mapped + f'_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_{kind}'))

    def test_native_editor_commands_start_without_opening_the_fixture(self):
        geany = next(editor for editor in report.EDITORS if editor['id'] == 'geany')
        self.assertEqual(command_for(geany, Path('/tmp/profile'), 1234),
                         ['geany', '--new-instance', '--no-session'])

    def test_minimal_electron_reuses_verified_lvce_runtime(self):
        lvce = next(e for e in report.EDITORS if e['id'] == 'lvce')
        electron = next(e for e in report.EDITORS if e['id'] == 'electron')
        for key in ['url', 'sha256', 'archive', 'binary']:
            self.assertEqual(lvce[key], electron[key])
        self.assertIn('/download/' + lvce['version'] + '/', lvce['url'])
        command = command_for(electron, Path('/tmp/private-home'), 1234)
        self.assertIn('--remote-debugging-port=1234', command)
        self.assertIn('--user-data-dir=/tmp/private-home/profile', command)
        self.assertNotIn('hello.txt', ' '.join(command))

    def test_minimal_electron_rejects_mismatched_runtime_before_replacement(self):
        electron = next(e for e in report.EDITORS if e['id'] == 'electron')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'app'
            def extract(*args, **kwargs):
                binary = target / electron['binary']
                binary.parent.mkdir(parents=True)
                binary.touch()
                config = binary.parent / 'resources/app/config.json'
                config.parent.mkdir(parents=True)
                config.write_text('{"electronVersion":"wrong"}')
            with patch.object(install.subprocess, 'run', side_effect=extract):
                with self.assertRaisesRegex(ValueError, 'version differs'):
                    install.extract(electron, Path('runtime.deb'), target)
            self.assertTrue((target / electron['binary']).parent.joinpath('resources/app/config.json').exists())

    def test_controlled_icon_experiment_rejects_bundle_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'usr/lib/lvce/resources/app/static/test/packages/icon-theme-worker/dist/iconThemeWorkerMain.js'
            bundle.parent.mkdir(parents=True)
            bundle.write_text('prefix\n' + compare_icon.ORIGINAL + '\nsuffix')
            evidence = compare_icon.patch_bundle(root)
            self.assertNotEqual(evidence['beforeSha256'], evidence['afterSha256'])
            self.assertEqual(bundle.read_text(), 'prefix\n' + compare_icon.CANDIDATE + '\nsuffix')
            with self.assertRaisesRegex(ValueError, 'differs'):
                compare_icon.patch_bundle(root)

    def test_controlled_ipc_patch_changes_only_the_expected_function_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'usr/lib/lvce/resources/app/static/test/packages/renderer-worker/dist/rendererWorkerMain.js'
            bundle.parent.mkdir(parents=True)
            bundle.write_text('prefix\n' + compare_ipc.ORIGINAL + '\nsuffix')
            evidence = compare_ipc.patch_bundle(root)
            self.assertNotEqual(evidence['beforeSha256'], evidence['afterSha256'])
            self.assertEqual(bundle.read_text(), 'prefix\n' + compare_ipc.CANDIDATE + '\nsuffix')
            with self.assertRaisesRegex(ValueError, 'differs'):
                compare_ipc.patch_bundle(root)

    def test_controlled_ipc_validation_requires_every_pair_and_raw_evidence(self):
        editor = json.loads((ROOT / 'config/ipc-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_ipc.ORIGINAL, 'after': compare_ipc.CANDIDATE},
                'pairs': pairs, 'summary': compare_ipc.compare_open.summarize(pairs)}
        compare_ipc.validate_experiment(data)
        for mutate in [lambda d: d['pairs'].pop(),
                       lambda d: d['pairs'][1].update(order=['baseline', 'candidate']),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'][0]['candidate'].update(rawCounter=''),
                       lambda d: d.update(summary={})]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_ipc.validate_experiment(broken)

    def test_minification_rejects_unguarded_inputs_before_launching_transform(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(compare_minify.subprocess, 'run') as transform:
            with self.assertRaisesRegex(ValueError, 'inputs differ'):
                compare_minify.patch_bundle(Path(temporary))
            transform.assert_not_called()

    def test_minification_requires_exact_output_and_raw_comparison_evidence(self):
        editor = json.loads((ROOT / 'config/minify-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_minify.ORIGINAL, 'after': compare_minify.CANDIDATE,
                    'inputs': json.loads((ROOT / 'config/minify-inputs.json').read_text()),
                    'outputs': json.loads((ROOT / 'config/minify-outputs.json').read_text())},
                'pairs': pairs, 'summary': compare_minify.compare_open.summarize(pairs)}
        compare_minify.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch']['outputs'].pop(),
                       lambda d: d['experimentalBundlePatch']['outputs'][0].update(sha256='0' * 64),
                       lambda d: d['experimentalBundlePatch']['inputs'][0].update(sha256='0' * 64),
                       lambda d: d['pairs'].pop(),
                       lambda d: d['pairs'][0]['candidate'].update(rawCounter='')]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_minify.validate_experiment(broken)

    def test_runtime_comparison_requires_unchanged_application_and_pinned_runtime(self):
        editor = json.loads((ROOT / 'config/runtime-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_runtime.ORIGINAL, 'after': compare_runtime.CANDIDATE,
                    'runtime': json.loads((ROOT / 'config/runtime-candidate.json').read_text()),
                    'applicationSha256': json.loads((ROOT / 'config/runtime-application.json').read_text())['sha256']},
                'pairs': pairs, 'summary': compare_runtime.compare_open.summarize(pairs)}
        compare_runtime.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch'].update(applicationSha256='0' * 64),
                       lambda d: d['experimentalBundlePatch']['runtime'].update(version='unknown'),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_runtime.validate_experiment(broken)

    def test_combined_comparison_requires_both_guarded_changes_and_raw_pairs(self):
        editor = json.loads((ROOT / 'config/minify-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_combined.ORIGINAL, 'after': compare_combined.CANDIDATE,
                    'runtime': {'before': compare_runtime.ORIGINAL, 'after': compare_runtime.CANDIDATE,
                        'runtime': json.loads((ROOT / 'config/runtime-candidate.json').read_text()),
                        'applicationSha256': json.loads((ROOT / 'config/runtime-application.json').read_text())['sha256']},
                    'minification': {'before': compare_minify.ORIGINAL, 'after': compare_minify.CANDIDATE,
                        'inputs': json.loads((ROOT / 'config/minify-inputs.json').read_text()),
                        'outputs': json.loads((ROOT / 'config/minify-outputs.json').read_text())}},
                'pairs': pairs, 'summary': compare_combined.compare_open.summarize(pairs)}
        compare_combined.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch'].pop('runtime'),
                       lambda d: d['experimentalBundlePatch'].pop('minification'),
                       lambda d: d['experimentalBundlePatch']['minification']['outputs'].pop(),
                       lambda d: d['pairs'][0]['candidate'].update(rawCounter=''),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_combined.validate_experiment(broken)

    def test_font_comparison_requires_exact_bundle_and_complete_raw_trials(self):
        editor = json.loads((ROOT / 'config/font-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': dict(json.loads((ROOT / 'config/font-bundle.json').read_text()),
                    before=compare_font.ORIGINAL, after=compare_font.CANDIDATE),
                'pairs': pairs, 'summary': compare_font.compare_open.summarize(pairs)}
        compare_font.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch'].update(afterSha256='0' * 64),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_font.validate_experiment(broken)

    def test_initial_render_comparison_requires_both_exact_bundle_patches(self):
        editor = json.loads((ROOT / 'config/render-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_render.ORIGINAL, 'after': compare_render.CANDIDATE,
                    'patches': json.loads((ROOT / 'config/render-patches.json').read_text())},
                'pairs': pairs, 'summary': compare_render.compare_open.summarize(pairs)}
        compare_render.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch']['patches'].pop(),
                       lambda d: d['experimentalBundlePatch']['patches'][0].update(afterSha256='0' * 64),
                       lambda d: d['pairs'][0]['candidate'].update(rawCounter=''),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_render.validate_experiment(broken)

    def test_plaintext_comparison_requires_lazy_tokenize_edit_and_dispose_guards(self):
        editor = json.loads((ROOT / 'config/plain-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_plain.ORIGINAL, 'after': compare_plain.CANDIDATE,
                    'patches': json.loads((ROOT / 'config/plain-patches.json').read_text())},
                'pairs': pairs, 'summary': compare_plain.compare_open.summarize(pairs)}
        compare_plain.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch']['patches'][0]['replacements'].pop(),
                       lambda d: d['experimentalBundlePatch']['patches'][0].update(afterSha256='0' * 64),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_plain.validate_experiment(broken)

    def test_cache_policy_comparison_requires_exact_patch_and_complete_raw_trials(self):
        editor = json.loads((ROOT / 'config/cache-baseline.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': {'before': compare_cache.ORIGINAL, 'after': compare_cache.CANDIDATE,
                    'patches': json.loads((ROOT / 'config/cache-patches.json').read_text())},
                'pairs': pairs, 'summary': compare_cache.compare_open.summarize(pairs)}
        compare_cache.validate_experiment(data)
        for mutate in [lambda d: d['experimentalBundlePatch']['patches'][0]['replacements'].pop(),
                       lambda d: d['experimentalBundlePatch']['patches'][0].update(afterSha256='0' * 64),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_cache.validate_experiment(broken)

    def test_minifier_comparison_guards_both_transforms_and_all_raw_pairs(self):
        editor = json.loads((ROOT / 'config/compact-baseline.json').read_text())
        inputs = json.loads((ROOT / 'config/minify-inputs.json').read_text())
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'baselineBundlePatch': {'before': compare_minify.ORIGINAL, 'after': compare_minify.CANDIDATE,
                    'inputs': inputs, 'outputs': json.loads((ROOT / 'config/minify-outputs.json').read_text())},
                'experimentalBundlePatch': {'before': compare_compact.ORIGINAL, 'after': compare_compact.CANDIDATE,
                    'inputs': inputs, 'outputs': json.loads((ROOT / 'config/compact-outputs.json').read_text())},
                'pairs': pairs, 'summary': compare_compact.compare_open.summarize(pairs)}
        compare_compact.validate_experiment(data)
        for mutate in [lambda d: d.pop('baselineBundlePatch'),
                       lambda d: d['baselineBundlePatch']['outputs'].pop(),
                       lambda d: d['experimentalBundlePatch']['outputs'][0].update(sha256='0' * 64),
                       lambda d: d['pairs'][0]['candidate'].update(rawCounter=''),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_compact.validate_experiment(broken)

    def test_lazy_comparison_guards_both_builds_and_post_cutoff_observation(self):
        editor = json.loads((ROOT / 'config/lazy-baseline.json').read_text())
        recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
        def observed(count):
            return dict(trial(count), editorWorkerScripts=['lvce://-/test/packages/editor-worker/dist/editorWorkerMain.js'], coldModuleParsed=False, observedAfterCutoff=True)
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': observed(200), 'candidate': observed(100)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'baselineBundlePatch': {'variant': 'baseline', 'recipe': recipe, 'assetIndexSha256': recipe['assetIndex']['inputSha256']},
                'experimentalBundlePatch': {'variant': 'candidate', 'recipe': recipe, 'assetIndexSha256': recipe['assetIndex']['outputSha256']},
                'pairs': pairs, 'summary': compare_lazy.compare_open.summarize(pairs)}
        compare_lazy.validate_experiment(data)
        for mutate in [lambda d: d.pop('baselineBundlePatch'),
                       lambda d: d['experimentalBundlePatch']['recipe'].update(node='wrong'),
                       lambda d: d['experimentalBundlePatch'].update(assetIndexSha256='wrong'),
                       lambda d: d['pairs'][0]['candidate'].update(observedAfterCutoff=False),
                       lambda d: d['pairs'][0]['candidate'].update(coldModuleParsed=True),
                       lambda d: d['pairs'][0]['candidate'].update(editorWorkerScripts=[]),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_lazy.validate_experiment(broken)

    def test_lazy_asset_index_preserves_policies_and_rejects_unexpected_chunks(self):
        recipe = json.loads((ROOT / 'config/lazy-build.json').read_text())
        spec = recipe['assetIndex']
        original = {'headers': [spec['headers'], {'Content-Security-Policy': "default-src 'none'; script-src 'self'"}],
                    'files': {spec['referenceFile']: 0, '/entryWorkerMain.js': 1}, 'version': 'fixture'}
        raw = (json.dumps(original, indent=2) + '\n').encode()
        spec['inputSha256'] = hashlib.sha256(raw).hexdigest()
        expected = copy.deepcopy(original)
        expected['files'].update({uri: 0 for uri in spec['additions']})
        spec['outputSha256'] = hashlib.sha256((json.dumps(expected, indent=2) + '\n').encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / spec['path']
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            compare_lazy.patch_asset_index(root, 'baseline', recipe)
            self.assertEqual(path.read_bytes(), raw)
            compare_lazy.patch_asset_index(root, 'candidate', recipe)
            self.assertEqual(json.loads(path.read_text()), expected)
            self.assertEqual(expected['headers'], original['headers'])
            for mutate in [lambda r: r['assetIndex'].update(inputSha256='wrong'),
                           lambda r: r['assetIndex'].update(outputSha256='wrong'),
                           lambda r: r['assetIndex']['additions'].pop(),
                           lambda r: r['assetIndex']['headers'].update(extra='wrong')]:
                path.write_bytes(raw)
                broken = copy.deepcopy(recipe)
                mutate(broken)
                with self.assertRaises(ValueError):
                    compare_lazy.patch_asset_index(root, 'candidate', broken)
                self.assertEqual(path.read_bytes(), raw)

    def test_summary_comparison_requires_exact_patch_and_worker_presence_evidence(self):
        editor = json.loads((ROOT / 'config/summary-baseline.json').read_text())
        def observed(count, problem):
            targets = [{'url': 'lvce://-/packages/editor-worker/dist/editorWorkerMain.js'}]
            if problem:
                targets.append({'url': 'lvce://-/packages/problems-view/dist/problemsViewWorkerMain.js'})
            return dict(trial(count), observedAfterCutoff=True, observedWorkerTargets=targets, problemsWorkerObserved=problem)
        pairs = [{'index': index, 'order': ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': observed(200, True), 'candidate': observed(100, False)} for index in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': compare_summary.SPEC, 'pairs': pairs,
                'summary': compare_summary.compare_open.summarize(pairs)}
        compare_summary.validate_experiment(data)
        for mutate in [lambda d: d.update(experimentalBundlePatch={}),
                       lambda d: d['pairs'][0]['candidate'].update(observedWorkerTargets=[]),
                       lambda d: d['pairs'][0]['candidate'].update(problemsWorkerObserved=True),
                       lambda d: d['pairs'][0]['candidate'].update(observedAfterCutoff=False),
                       lambda d: d['pairs'][0]['candidate'].update(documentContent='wrong'),
                       lambda d: d['pairs'].pop()]:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.assertRaises(ValueError):
                compare_summary.validate_experiment(broken)

    def test_mapped_capture_retains_only_live_maps_and_preserves_existing_paths(self):
        import profile_mapped
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maps = root / 'private'
            maps.mkdir()
            target = root / 'perf-tmp'
            target.mkdir()
            (maps / 'perf-1234.map').write_text('1000 20 JS:~loadContent lvce://-/packages/editor-worker/main.js')
            (maps / 'perf-9999.map').write_text('exited process')
            capture = object.__new__(profile_mapped.MappedCapture)
            capture.map_directory = maps
            capture.map_links = []
            capture.maps_ready = False
            capture.pids = lambda: [1234]
            with patch.object(profile_mapped.ProfiledCapture, 'snapshot'), \
                 patch.object(profile_mapped.ProfiledCapture, '__exit__'), \
                 patch.object(profile_mapped, 'Path', side_effect=lambda value: target if value == '/tmp' else Path(value)):
                capture.snapshot('after')
                self.assertTrue(capture.maps_ready)
                self.assertTrue((target / 'perf-1234.map').is_symlink())
                self.assertFalse((target / 'perf-9999.map').exists())
                capture.__exit__(None, None, None)
                self.assertFalse((target / 'perf-1234.map').exists())
                (target / 'perf-1234.map').write_text('unrelated existing data')
                capture.map_links = []
                with self.assertRaises(FileExistsError): capture.snapshot('after')
                capture.__exit__(None, None, None)
                self.assertEqual((target / 'perf-1234.map').read_text(), 'unrelated existing data')

    def test_diff_comparison_requires_frozen_worker_contract_and_all_pairs(self):
        import compare_diff
        editor = json.loads((ROOT / 'config/diff-baseline.json').read_text())
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for i in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': compare_diff.SPEC, 'pairs': pairs,
                'summary': compare_diff.compare_open.summarize(pairs)}
        compare_diff.validate_experiment(data)
        for change in ['contract', 'count', 'content', 'summary']:
            broken = copy.deepcopy(data)
            if change == 'contract': broken['experimentalBundlePatch']['workerContract']['sha256'] = 'wrong'
            if change == 'count': broken['pairs'].pop()
            if change == 'content': broken['pairs'][0]['candidate']['documentContent'] = 'wrong'
            if change == 'summary': broken['summary']['meanPairedInstructionsSaved'] = 0
            with self.subTest(change=change), self.assertRaises(ValueError):
                compare_diff.validate_experiment(broken)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / compare_diff.SPEC['workerContract']['path']
            worker.parent.mkdir(parents=True)
            worker.write_text('different render contract')
            target = root / compare_diff.SPEC['path']
            target.parent.mkdir(parents=True)
            target.write_text('untouched renderer')
            with self.assertRaisesRegex(ValueError, 'freshness contract'):
                compare_diff.patch_bundle(root)
            self.assertEqual(target.read_text(), 'untouched renderer')

    def test_response_comparison_requires_full_frozen_patch_and_all_raw_trials(self):
        import compare_response
        editor = json.loads((ROOT / 'config/response-baseline.json').read_text())
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(200), 'candidate': trial(100)} for i in range(40)]
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
                'experimentalBundlePatch': compare_response.SPEC, 'pairs': pairs,
                'summary': compare_response.compare_open.summarize(pairs)}
        compare_response.validate_experiment(data)
        for change in ['patch', 'count', 'content', 'order', 'summary']:
            broken = copy.deepcopy(data)
            if change == 'patch': broken['experimentalBundlePatch']['afterSha256'] = 'wrong'
            if change == 'count': broken['pairs'].pop()
            if change == 'content': broken['pairs'][0]['candidate']['documentContent'] = 'wrong'
            if change == 'order': broken['pairs'][0]['order'].reverse()
            if change == 'summary': broken['summary']['meanPairedInstructionsSaved'] = 0
            with self.subTest(change=change), self.assertRaises(ValueError):
                compare_response.validate_experiment(broken)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / compare_response.SPEC['path']
            target.parent.mkdir(parents=True)
            target.write_text('unexpected application')
            with self.assertRaises(ValueError): compare_response.patch_bundle(root)
            self.assertEqual(target.read_text(), 'unexpected application')

    def test_lazy_worker_observation_requires_acknowledged_counter_cutoff(self):
        capture = object.__new__(compare_lazy.VerifiedCapture)
        with patch.object(compare_lazy.Capture, 'finish', return_value={'cutoffAcknowledged': False}), \
             patch.object(compare_lazy, 'Page') as page:
            with self.assertRaisesRegex(ValueError, 'acknowledged cutoff'):
                capture.finish()
            page.assert_not_called()

    def test_lazy_observation_survives_unresponsive_worker_but_requires_known_scripts(self):
        for has_editor in [False, True]:
            with self.subTest(has_editor=has_editor), tempfile.TemporaryDirectory() as directory:
                page = MagicMock()
                page.counter = 0
                messages = []

                def call(method, **params):
                    page.counter += 1
                    if method == 'Page.getFrameTree':
                        return {'frameTree': {'frame': {'id': 'page'}}}
                    if method == 'Target.getTargets':
                        return {'targetInfos': [{'type': 'worker', 'targetId': target}
                                                for target in ['idle', 'editor']]}
                    if method == 'Target.attachToTarget':
                        return {'sessionId': params['targetId']}
                    return {}

                def send(raw):
                    request = json.loads(raw)
                    if request['method'] == 'Target.setAutoAttach':
                        self.assertEqual(request['sessionId'], 'page')
                        self.assertFalse(request['params']['waitForDebuggerOnStart'])
                        messages.append({'id': request['id'], 'sessionId': 'page', 'result': {}})
                    elif request['sessionId'] == 'idle':
                        messages.append(TimeoutError('idle worker did not answer'))
                    else:
                        if request['method'] == 'Debugger.enable':
                            url = ('lvce://-/packages/editor-worker/dist/editorWorkerMain.js'
                                   if has_editor else 'lvce://-/unrelated.js')
                            messages.append({'sessionId': 'editor', 'method': 'Debugger.scriptParsed',
                                             'params': {'url': url}})
                        messages.append({'id': request['id'], 'sessionId': 'editor', 'result': {}})

                def receive():
                    message = messages.pop(0)
                    if isinstance(message, Exception):
                        raise message
                    return json.dumps(message)

                page.call.side_effect = call
                page.socket.send.side_effect = send
                page.socket.recv.side_effect = receive
                capture = object.__new__(compare_lazy.VerifiedCapture)
                capture.debug_port = 1234
                with patch.object(compare_lazy.Capture, 'finish', return_value={'cutoffAcknowledged': True}), \
                     patch.object(compare_lazy, 'Page', return_value=page), \
                     patch.object(compare_lazy, 'Path', side_effect=lambda value: Path(directory) / value):
                    if has_editor:
                        result = capture.finish()
                        self.assertTrue(result['observedAfterCutoff'])
                        self.assertFalse(result['coldModuleParsed'])
                    else:
                        with self.assertRaisesRegex(ValueError, 'did not find editor worker'):
                            capture.finish()
                evidence = json.loads((Path(directory) / 'results/lazy-worker-observation.json').read_text())
                self.assertIn('TimeoutError', evidence['observations'][0]['error'])
                self.assertEqual(len(evidence['observations']), 2)
                page.close.assert_called_once()

    def test_controlled_ipc_cli_keeps_package_identity_separate_from_launch_paths(self):
        editor = json.loads((ROOT / 'config/ipc-baseline.json').read_text())
        def fake_trial(entry, perf):
            self.assertTrue(Path(entry['binary']).is_absolute())
            return dict(trial(100 if 'candidate' in entry['binary'] else 200), command=[entry['binary']])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/ipc-baseline.json').write_text(json.dumps(editor))
            (root / '.tmp/apps').mkdir(parents=True)
            previous = Path.cwd()
            try:
                os.chdir(root)
                with (patch.object(compare_ipc, 'ROOT', root),
                      patch.object(sys, 'argv', ['compare_ipc.py', '--editor', 'lvce', '--perf', '/perf']),
                      patch.object(compare_ipc.platform, 'machine', return_value='aarch64'),
                      patch.object(install, 'download'), patch.object(install, 'extract'),
                      patch.object(compare_ipc, 'patch_bundle', return_value={'before': compare_ipc.ORIGINAL, 'after': compare_ipc.CANDIDATE}),
                      patch.object(benchmark, 'trial', side_effect=fake_trial),
                      patch.object(compare_ipc.subprocess, 'check_output', return_value='perf test'),
                      patch('builtins.print')):
                    compare_ipc.main()
                data = json.loads((root / 'results/ipc-comparison.json').read_text())
                self.assertEqual(data['baseline'], editor)
                self.assertEqual(data['candidate'], editor)
                self.assertEqual(len(data['pairs']), 40)
            finally:
                os.chdir(previous)

    def test_zed_launch_is_native_and_uses_isolated_data_without_fixture(self):
        zed = next(editor for editor in report.EDITORS if editor['id'] == 'zed')
        self.assertEqual(command_for(zed, Path('/tmp/profile'), 1234),
                         [str(ROOT / '.tmp/apps/zed' / zed['binary']),
                          '--user-data-dir', '/tmp/profile/data/zed'])

    def test_copy_path_verification_rejects_stale_clipboard(self):
        expected = '/tmp/trial/hello.txt'
        results = [subprocess.CompletedProcess(['xclip'], 0, CONTENT, ''),
                   subprocess.CompletedProcess(['xclip'], 0, expected, '')]
        with patch.object(benchmark.subprocess, 'run', side_effect=results), \
                patch.object(benchmark.time, 'sleep'):
            self.assertEqual(benchmark.read_clipboard_contents(expected=expected), expected)
        with patch.object(benchmark.subprocess, 'run', return_value=results[0]):
            with self.assertRaises(RuntimeError):
                benchmark.read_clipboard_contents(timeout=0, expected=expected)

    def test_clipboard_verification_waits_for_delayed_editor_copy(self):
        results = [subprocess.CompletedProcess(['xclip'], 1, '', 'no selection'),
                   subprocess.CompletedProcess(['xclip'], 0, CONTENT, '')]
        with patch.object(benchmark.subprocess, 'run', side_effect=results), \
                patch.object(benchmark.time, 'sleep'):
            self.assertEqual(benchmark.read_clipboard_contents(timeout=1), CONTENT)

    def test_failure_diagnostics_run_before_editor_cleanup(self):
        events = []
        capture = MagicMock()
        capture.__enter__.return_value = capture
        capture.__exit__.side_effect = lambda *args: events.append('cleanup') or False
        capture.launch.side_effect = RuntimeError('launch failed')

        def inspect_windows(*args, **kwargs):
            events.append('diagnostics')
            return subprocess.CompletedProcess(args[0], 0, stdout='live window tree')

        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.object(benchmark, 'Capture', return_value=capture), \
                        patch.object(benchmark.subprocess, 'run', side_effect=inspect_windows):
                    data = benchmark.trial({'id': 'lvce', 'binary': 'lvce'}, 'perf')
            finally:
                os.chdir(original_directory)
        self.assertEqual(events, ['diagnostics', 'cleanup'])
        self.assertFalse(data['valid'])
        self.assertEqual(data['error'], 'launch failed')
        self.assertEqual(data['diagnostics']['windowTree'], 'live window tree')

    def test_counter_rejects_unsupported_zero_and_multiplexing(self):
        self.assertEqual(parse_counter('123;;instructions;group;100;100.00;;'), 123)
        for count, running in [('<not supported>', '100'), ('0', '100'), ('123', '50'), ('123', 'nan')]:
            with self.subTest(count=count, running=running), self.assertRaises(ValueError):
                parse_counter(f'{count};;instructions;group;100;{running};;')

    def test_report_rejects_missing_open_or_content_evidence(self):
        for editor in report.EDITORS:
            data = result(editor)
            report.validate_result(data, editor['id'])
            invalid = [{'documentLoaded': False}, {'documentContent': ''}, {'cutoffAcknowledged': False},
                       {'focusPid': 999}, {'instructions': 0}, {'valid': False}]
            if editor['id'] in {'lvce', 'electron', 'zed'}:
                invalid.append({'documentPath': '/tmp/other/hello.txt'})
            for changes in invalid:
                broken = copy.deepcopy(data)
                broken['trials'][0].update(changes)
                with self.subTest(editor=editor['id'], changes=changes), self.assertRaises(ValueError):
                    report.validate_result(broken, editor['id'])
        with self.assertRaises(ValueError):
            report.validate_result({'editor': report.EDITORS[0], 'trials': [trial()]}, report.EDITORS[0]['id'])

    def test_identity_verification_is_specific_to_editor(self):
        for editor in report.EDITORS:
            data = result(editor)
            if editor['id'] in {'lvce', 'electron', 'zed'}:
                data['trials'][0]['windowTitle'] = 'Lvce Editor'
                report.validate_result(data, editor['id'])
                data['trials'][0]['documentPath'] = '/tmp/wrong/hello.txt'
            else:
                data['trials'][0]['windowTitle'] = editor['name']
            with self.subTest(editor=editor['id']), self.assertRaises(ValueError):
                report.validate_result(data, editor['id'])

    def test_report_requires_complete_matrix_and_matching_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                report.load_results(root)
            for editor in report.EDITORS:
                path = root / ('open-' + editor['id'])
                path.mkdir()
                (path / 'results.json').write_text(json.dumps(result(editor)))
            self.assertEqual(len(report.load_results(root)), len(report.EDITORS))
            path = root / ('open-' + report.EDITORS[0]['id']) / 'results.json'
            path.write_text(json.dumps(result(dict(report.EDITORS[0], version='wrong'))))
            with self.assertRaises(ValueError):
                report.load_results(root)
            geany = next(editor for editor in report.EDITORS if editor['id'] == 'geany')
            path = root / ('open-' + geany['id']) / 'results.json'
            broken = result(geany)
            broken['host']['packageVersion'] = 'wrong'
            path.write_text(json.dumps(broken))
            with self.assertRaises(ValueError):
                report.load_results(root)

    def test_paired_report_requires_matching_artifacts_and_valid_raw_trials(self):
        from compare_open import summarize
        baseline = json.loads((ROOT / 'config/lvce-baseline.json').read_text())
        candidate = next(editor for editor in report.EDITORS if editor['id'] == 'lvce')
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(120), 'candidate': trial(100)} for i in range(40)]
        data = {'baseline': baseline, 'candidate': candidate, 'protocol': PROTOCOL,
                'host': {'machine': 'aarch64'}, 'pairs': pairs, 'summary': summarize(pairs)}
        self.assertEqual(report.validate_comparison(data, PROTOCOL), data)
        for change in ['artifact', 'trial', 'order', 'summary', 'count']:
            broken = copy.deepcopy(data)
            if change == 'artifact': broken['candidate']['sha256'] = 'wrong'
            if change == 'trial': broken['pairs'][0]['candidate']['documentContent'] = 'wrong'
            if change == 'order': broken['pairs'][0]['order'].reverse()
            if change == 'summary': broken['summary']['meanPairedInstructionsSaved'] = 0
            if change == 'count': broken['pairs'].pop()
            with self.subTest(change=change), self.assertRaises(ValueError):
                report.validate_comparison(broken, PROTOCOL)
        with tempfile.TemporaryDirectory() as directory:
            report.build_report([{'editor': candidate, 'data': result(candidate)}], Path(directory), 'run', 'sha', data)
            self.assertEqual(json.loads((Path(directory) / 'raw/paired-lvce.json').read_text()), data)
            self.assertIn('Paired official LVCE releases', (Path(directory) / 'index.html').read_text())

    def test_report_cli_validates_controlled_run_identity_and_publishes_all_raw_pairs(self):
        from compare_open import summarize
        baseline = json.loads((ROOT / 'config/lvce-baseline.json').read_text())
        candidate = next(editor for editor in report.EDITORS if editor['id'] == 'lvce')
        ipc_pin = json.loads((ROOT / 'config/ipc-baseline.json').read_text())
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(120), 'candidate': trial(100)} for i in range(40)]
        host = {'machine': 'aarch64', 'commit': 'commit', 'runUrl': 'https://example.test/run'}
        comparison = {'baseline': baseline, 'candidate': candidate, 'protocol': PROTOCOL,
                      'host': host, 'pairs': pairs, 'summary': summarize(pairs)}
        ipc = dict(comparison, baseline=ipc_pin, candidate=ipc_pin,
                   experimentalBundlePatch={'before': compare_ipc.ORIGINAL, 'after': compare_ipc.CANDIDATE})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/lvce-baseline.json').write_text(json.dumps(baseline))
            (root / 'docs').mkdir()
            (root / 'docs/ipc-analysis.html').write_text('<h1>Message analysis</h1>')
            for editor in report.EDITORS:
                path = root / f"results/editors/open-{editor['id']}/results.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(result(editor)))
            for folder, name, data in [('paired', 'comparison.json', comparison), ('ipc', 'ipc-comparison.json', ipc)]:
                path = root / 'results' / folder / name
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(data))
            with (patch.object(report, 'ROOT', root),
                  patch.object(sys, 'argv', ['report.py', '--run-url', host['runUrl'], '--commit', host['commit']]),
                  patch('builtins.print')):
                report.main()
                self.assertEqual(json.loads((root / '.tmp/pages/raw/paired-ipc.json').read_text()), ipc)
                self.assertEqual(json.loads((root / '.tmp/pages/report.json').read_text())['controlledIpcComparison'], ipc)
                self.assertIn('patched bundle', (root / '.tmp/pages/index.html').read_text())
                self.assertEqual((root / '.tmp/pages/ipc-analysis.html').read_text(), '<h1>Message analysis</h1>')
                ipc['host'] = dict(host, commit='wrong')
                (root / 'results/ipc/ipc-comparison.json').write_text(json.dumps(ipc))
                with self.assertRaisesRegex(ValueError, 'different workflow'):
                    report.main()

    def test_shipped_render_requires_official_pins_forty_pairs_and_current_run(self):
        import compare_shipped_render
        from compare_open import summarize
        baseline = json.loads(compare_shipped_render.BASELINE.read_text())
        candidate = next(editor for editor in report.EDITORS if editor['id'] == 'lvce')
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(1_420_000_000), 'candidate': trial(1_390_000_000)} for i in range(40)]
        data = {'baseline': baseline, 'candidate': candidate, 'protocol': PROTOCOL,
                'host': {'machine': 'aarch64', 'runUrl': 'run', 'commit': 'sha'},
                'pairs': pairs, 'summary': summarize(pairs)}
        self.assertEqual(compare_shipped_render.validate_experiment(data, 'run', 'sha'), data)
        for change in ['baseline', 'pairs', 'content', 'summary', 'run', 'commit', 'patch']:
            broken = copy.deepcopy(data)
            if change == 'baseline': broken['baseline']['sha256'] = 'wrong'
            if change == 'pairs': broken['pairs'].pop()
            if change == 'content': broken['pairs'][0]['candidate']['documentContent'] = 'wrong'
            if change == 'summary': broken['summary']['candidateMedian'] = 1
            if change == 'run': broken['host']['runUrl'] = 'old run'
            if change == 'commit': broken['host']['commit'] = 'old sha'
            if change == 'patch': broken['experimentalBundlePatch'] = {}
            with self.subTest(change=change), self.assertRaises(ValueError):
                compare_shipped_render.validate_experiment(broken, 'run', 'sha')
        overall_pairs = copy.deepcopy(pairs)
        for pair in overall_pairs: pair['candidate'] = trial(1_400_000_000)
        overall = dict(data, baseline=json.loads((ROOT/'config/lvce-baseline.json').read_text()),
                       pairs=overall_pairs, summary=summarize(overall_pairs))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            report.build_report([{'editor': candidate, 'data': result(candidate)}], output, 'run', 'sha',
                                overall, shipped_comparison=data)
            published = json.loads((output/'report.json').read_text())
            self.assertEqual(published['shippedRenderComparison'], data)
            self.assertEqual(json.loads((output/'raw/paired-shipped-render.json').read_text()), data)
            self.assertEqual(published['lvceTarget']['candidateMedian'], 1_400_000_000)
            self.assertFalse(published['lvceTarget']['met'])
            self.assertIn('Shipped initial-render comparison', (output/'index.html').read_text())

    def test_report_preserves_controlled_pairs_and_explicit_unmet_target(self):
        import compare_response
        from compare_open import summarize
        editor = json.loads((ROOT / 'config/response-baseline.json').read_text())
        pairs = [{'index': i, 'order': ['baseline', 'candidate'] if i % 2 == 0 else ['candidate', 'baseline'],
                  'baseline': trial(1_500_000_000), 'candidate': trial(1_400_000_000)} for i in range(40)]
        host = {'machine': 'aarch64', 'commit': 'sha', 'runUrl': 'https://example.test/run'}
        data = {'baseline': editor, 'candidate': editor, 'protocol': PROTOCOL, 'host': host,
                'experimentalBundlePatch': compare_response.SPEC, 'pairs': pairs, 'summary': summarize(pairs)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'paired-response/response-comparison.json'
            path.parent.mkdir();path.write_text(json.dumps(data))
            with patch.object(report, 'EXPERIMENTS', [('response', 'Buffered response')]):
                experiments = report.load_experiments(root, host['runUrl'], host['commit'])
                with self.assertRaisesRegex(ValueError, 'different workflow'): report.load_experiments(root, 'wrong', host['commit'])
                broken = copy.deepcopy(data);broken['pairs'].pop();path.write_text(json.dumps(broken))
                with self.assertRaises(ValueError): report.load_experiments(root, host['runUrl'], host['commit'])
            candidate = next(editor for editor in report.EDITORS if editor['id'] == 'lvce')
            official = dict(data, candidate=candidate)
            report.build_report([{'editor': candidate, 'data': result(candidate)}], root/'pages', host['runUrl'], 'sha', official, experiments=experiments)
            output = json.loads((root/'pages/report.json').read_text())
            self.assertFalse(output['lvceTarget']['met'])
            self.assertEqual(output['lvceTarget']['candidateMedian'], 1_400_000_000)
            self.assertEqual(json.loads((root/'pages/raw/paired-response.json').read_text()), data)
            self.assertEqual(output['controlledExperiments'][0]['raw'], 'raw/paired-response.json')
            self.assertIn('target unmet', (root/'pages/index.html').read_text())

    def test_report_keeps_raw_evidence_and_reports_medians(self):
        items = [dict(editor=editor, data=result(editor, counts)) for editor, counts in
                 [(report.EDITORS[0], [100, 300, 200]), (report.EDITORS[1], [50, 40, 60])]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report.build_report(items, root, 'https://example.test/run', 'commit')
            payload = json.loads((root / 'report.json').read_text())
            self.assertEqual([entry['medianInstructions'] for entry in payload['editors']], [50, 200])
            self.assertEqual(json.loads((root / 'raw/lvce.json').read_text()), items[0]['data'])
            page = (root / 'index.html').read_text()
            self.assertIn('hello world', page)
            self.assertIn('Geany', page)
            self.assertIn('Median instructions', page)
            self.assertIn('Download JSON', page)


if __name__ == '__main__':
    unittest.main()
