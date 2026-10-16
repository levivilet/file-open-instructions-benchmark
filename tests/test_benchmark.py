import copy
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
            if editor['id'] in {'lvce', 'zed'}:
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
            if editor['id'] in {'lvce', 'zed'}:
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
