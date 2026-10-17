"""Native-only diagnostic profiles, separate from official benchmark results."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import time

import benchmark
import install
import profile_open


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', choices=['lvce', 'electron'], default='lvce')
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats != 3 or platform.machine() != 'aarch64':
        parser.error('native diagnosis requires three fresh-profile ARM64 trials')
    config = 'preferences-baseline.json' if args.editor == 'lvce' else 'native-reference.json'
    editor = json.loads((install.ROOT / 'config' / config).read_text())
    if editor['id'] != args.editor:
        raise ValueError('native profile editor identity mismatch')
    baseline = json.loads((install.ROOT / 'config/preferences-baseline.json').read_text())
    if any(editor[key] != baseline[key] for key in ['archive', 'url', 'sha256', 'binary']):
        raise ValueError('native reference must use the exact LVCE runtime archive')
    archive = install.ROOT / '.tmp/apps' / editor['archive']
    archive.parent.mkdir(parents=True, exist_ok=True)
    install.download(editor, archive)
    directory = install.ROOT / '.tmp/apps' / (args.editor + '-native')
    install.extract(editor, archive, directory)
    installed = dict(editor, binary=str(directory / editor['binary']))
    os.environ['PROFILE_NATIVE_ONLY'] = '1'
    benchmark.Capture = profile_open.ProfiledCapture
    original_trigger = benchmark.trigger_open
    stages = []

    def observe_trigger(editor_id, window_id, details):
        start = time.monotonic()
        window = original_trigger(editor_id, window_id, details)
        stages.append({'openCommandStart': start, 'dialogFocusedAt': time.monotonic()})
        return window

    benchmark.trigger_open = observe_trigger
    trials = []
    try:
        for _ in range(args.repeats):
            stages.clear()
            trial = benchmark.trial(installed, args.perf)
            trial['diagnosticStages'] = list(stages)
            trials.append(trial)
    finally:
        benchmark.trigger_open = original_trigger
    Path('results').mkdir(exist_ok=True)
    Path('results/native-results.json').write_text(json.dumps({
        'editor': editor, 'protocol': benchmark.PROTOCOL,
        'host': {'machine': platform.machine(), 'kernel': platform.release(),
                 'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
                 'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')},
        'design': 'sampled diagnostic only; three fresh profiles; ordinary protocol; never official report input',
        'trials': trials,
    }, indent=2) + '\n')
    if not all(trial['valid'] for trial in trials):
        raise SystemExit('invalid native diagnostic trial')


if __name__ == '__main__':
    main()
