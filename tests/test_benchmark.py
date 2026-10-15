import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark import CONTENT, PROTOCOL, RESPONSE_WINDOW_SECONDS, is_main_window
from counters import parse_counter
import report


def trial(count=100):
    return {'valid': True, 'instructions': count, 'measurementSeconds': 1.02,
            'openDispatchSeconds': .02, 'openToCutoffSeconds': 1.0,
            'source': 'perf stat instructions, per-CPU cgroup',
            'documentLoaded': True, 'documentContent': CONTENT,
            'windowTitle': 'hello.txt - LVCE Editor', 'focusPid': 12,
            'pidsAtOpen': [12, 14], 'readiness': {'windowId': '1'},
            'cutoffAcknowledged': True,
            'rawCounter': f'{count};;instructions;open-test;1234;100.00;;\n'}


def result(editor, counts=(100, 300, 200)):
    return {'editor': editor, 'protocol': PROTOCOL, 'host': {'machine': 'aarch64'},
            'trials': [dict(trial(count), repeat=index + 1) for index, count in enumerate(counts)]}


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

    def test_counter_rejects_unsupported_zero_and_multiplexing(self):
        self.assertEqual(parse_counter('123;;instructions;group;100;100.00;;'), 123)
        for count, running in [('<not supported>', '100'), ('0', '100'), ('123', '50'), ('123', 'nan')]:
            with self.subTest(count=count, running=running), self.assertRaises(ValueError):
                parse_counter(f'{count};;instructions;group;100;{running};;')

    def test_report_rejects_missing_open_or_content_evidence(self):
        editor = report.EDITORS[0]
        data = result(editor)
        report.validate_result(data, editor['id'])
        invalid = [{'documentLoaded': False}, {'documentContent': ''},
                   {'windowTitle': 'Untitled'}, {'cutoffAcknowledged': False},
                   {'focusPid': 999}, {'instructions': 0}, {'valid': False}]
        for changes in invalid:
            broken = copy.deepcopy(data)
            broken['trials'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                report.validate_result(broken, editor['id'])
        with self.assertRaises(ValueError):
            report.validate_result({'editor': editor, 'trials': [trial()]}, editor['id'])

    def test_report_requires_complete_matrix_and_matching_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                report.load_results(root)
            for editor in report.EDITORS:
                path = root / ('open-' + editor['id'])
                path.mkdir()
                (path / 'results.json').write_text(json.dumps(result(editor)))
            self.assertEqual(len(report.load_results(root)), 2)
            path = root / ('open-' + report.EDITORS[0]['id']) / 'results.json'
            path.write_text(json.dumps(result(dict(report.EDITORS[0], version='wrong'))))
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
            self.assertIn('Median instructions', page)
            self.assertIn('Download JSON', page)


if __name__ == '__main__':
    unittest.main()
