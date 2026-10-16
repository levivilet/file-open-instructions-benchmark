"""Native-only diagnostic profile of the current official baseline."""
import json
import os
from pathlib import Path
import sys
import benchmark
import install
import profile_open

os.environ['PROFILE_NATIVE_ONLY'] = '1'
editor = json.loads((install.ROOT / 'config/preferences-baseline.json').read_text())
archive = install.ROOT / '.tmp/apps' / editor['archive']
archive.parent.mkdir(parents=True, exist_ok=True)
install.download(editor, archive)
install.extract(editor, archive, install.ROOT / '.tmp/apps/lvce-native')
editor = dict(editor, binary=str(install.ROOT / '.tmp/apps/lvce-native' / editor['binary']))
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--editor')
parser.add_argument('--perf', required=True)
parser.add_argument('--repeats', type=int, default=3)
args = parser.parse_args()
benchmark.Capture = profile_open.ProfiledCapture
trials = [benchmark.trial(editor, args.perf) for _ in range(args.repeats)]
Path('results/native-results.json').write_text(json.dumps({'editor': editor, 'protocol': benchmark.PROTOCOL, 'trials': trials}, indent=2) + '\n')
if not all(trial['valid'] for trial in trials):
    raise SystemExit('invalid native diagnostic trial')
