"""Build a static, validated file-open instruction comparison."""
import argparse
import html
import json
import math
from pathlib import Path
import statistics

from benchmark import CONTENT, RESPONSE_WINDOW_SECONDS
from counters import parse_counter

ROOT = Path(__file__).resolve().parent.parent
EDITORS = json.loads((ROOT / 'config/editors.lock.json').read_text())


def validate_result(data, editor_id):
    if not isinstance(data, dict) or not isinstance(data.get('editor'), dict) or data['editor'].get('id') != editor_id:
        raise ValueError(f'{editor_id}: result has the wrong editor identity')
    trials = data.get('trials')
    if not isinstance(trials, list) or len(trials) < 3:
        raise ValueError(f'{editor_id}: result has fewer than three trials')
    for index, trial in enumerate(trials, 1):
        if not isinstance(trial, dict) or trial.get('valid') is not True:
            raise ValueError(f'{editor_id}: trial {index} is invalid')
        for key in ('instructions', 'measurementSeconds', 'openDispatchSeconds', 'openToCutoffSeconds'):
            value = trial.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{editor_id}: trial {index} has invalid {key}')
        if (trial['instructions'] <= 0 or not isinstance(trial['instructions'], int)
                or trial['measurementSeconds'] < RESPONSE_WINDOW_SECONDS
                or trial['openDispatchSeconds'] <= 0
                or trial['openToCutoffSeconds'] < RESPONSE_WINDOW_SECONDS):
            raise ValueError(f'{editor_id}: trial {index} has out-of-range measurements')
        if (trial.get('cutoffAcknowledged') is not True
                or trial.get('documentLoaded') is not True
                or trial.get('documentContent') != CONTENT
                or (editor_id in {'vscode', 'geany'} and 'hello.txt' not in trial.get('windowTitle', ''))
                or (editor_id in {'lvce', 'electron'} and (not trial.get('fixture', '').endswith('/hello.txt')
                                             or trial.get('documentPath') != trial['fixture']))
                or not trial.get('readiness')
                or not isinstance(trial.get('focusPid'), int)
                or trial.get('focusPid') not in trial.get('pidsAtOpen', [])
                or parse_counter(trial.get('rawCounter', '')) != trial['instructions']):
            raise ValueError(f'{editor_id}: missing document, focus, cutoff or raw counter evidence')
        if not isinstance(trial.get('source'), str) or not trial['source']:
            raise ValueError(f'{editor_id}: trial {index} is missing its measurement source')
    if not isinstance(data.get('protocol'), dict) or not isinstance(data.get('host'), dict):
        raise ValueError(f'{editor_id}: result is missing protocol or host metadata')
    return data


def load_results(results_root):
    paths = list(results_root.glob('open-*/results.json'))
    found = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
            editor_id = data['editor']['id']
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f'malformed benchmark artifact {path}: {error}') from error
        if path.parent.name != f'open-{editor_id}' or editor_id in found:
            raise ValueError(f'misnamed or duplicate benchmark artifact for {editor_id}')
        found[editor_id] = path
    expected = {editor['id'] for editor in EDITORS}
    if found.keys() != expected:
        raise ValueError(f'editor artifacts do not match matrix (missing: {sorted(expected - found.keys())}; '
                         f'extra: {sorted(found.keys() - expected)})')
    results = []
    for editor in EDITORS:
        data = validate_result(json.loads(found[editor['id']].read_text()), editor['id'])
        if data['editor'] != editor or data['host'].get('machine') != 'aarch64':
            raise ValueError(f"{editor['id']}: version or architecture mismatch")
        if editor.get('package') and data['host'].get('packageVersion') != editor['version']:
            raise ValueError(f"{editor['id']}: installed package version mismatch")
        if results and data['protocol'] != results[0]['data']['protocol']:
            raise ValueError('mixed measurement protocols')
        results.append({'editor': editor, 'data': data})
    return results


def build_report(results, output, run_url, commit):
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / 'raw'
    raw_dir.mkdir(exist_ok=True)
    entries, rows = [], []
    for item in sorted(results, key=lambda item: statistics.median(
            trial['instructions'] for trial in item['data']['trials'])):
        editor, data = item['editor'], item['data']
        trials = data['trials']
        median = statistics.median(trial['instructions'] for trial in trials)
        filename = f"{editor['id']}.json"
        (raw_dir / filename).write_text(json.dumps(data, indent=2) + '\n')
        entry = {'id': editor['id'], 'name': editor['name'], 'version': editor['version'],
                 'medianInstructions': median,
                 'trials': [{'repeat': trial.get('repeat'), 'instructions': trial['instructions'],
                             'measurementSeconds': trial['measurementSeconds'],
                             'openDispatchSeconds': trial['openDispatchSeconds'],
                             'openToCutoffSeconds': trial['openToCutoffSeconds'],
                             'source': trial['source']} for trial in trials],
                 'protocol': data['protocol'], 'host': data['host'], 'raw': f'raw/{filename}'}
        entries.append(entry)
        values = '<br>'.join(f"{trial['instructions'] / 1e6:.1f}M" for trial in trials)
        rows.append(f"<tr><th scope='row'>{html.escape(editor['name'])}</th>"
                    f"<td>{html.escape(editor['version'])}</td><td>{median / 1e6:.1f}M</td>"
                    f"<td>{values}</td><td><a href='raw/{html.escape(filename)}' download>Download JSON</a></td></tr>")
    report = {'complete': True, 'runUrl': run_url, 'commit': commit, 'editors': entries}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    maximum = max(entry['medianInstructions'] for entry in entries)
    chart_max = max(1, math.ceil(maximum * 1.1))
    chart_width = 900
    slot_width = chart_width / len(entries)
    bar_width = min(180, slot_width * .72)
    bars = []
    for index, entry in enumerate(entries):
        x = slot_width * index + (slot_width - bar_width) / 2
        bar_height = 240 * entry['medianInstructions'] / chart_max
        bars.append(f"<g><rect x='{x:.1f}' y='{280 - bar_height:.1f}' width='{bar_width:.1f}' height='{bar_height:.1f}'/>")
        bars.append(f"<text x='{x + bar_width / 2:.1f}' y='315' text-anchor='middle'>{html.escape(entry['name'])}</text>")
        bars.append(f"<text x='{x + bar_width / 2:.1f}' y='{270 - bar_height:.1f}' text-anchor='middle'>"
                    f"{entry['medianInstructions'] / 1e6:.1f}M</text></g>")
    safe_run, safe_commit = html.escape(run_url, quote=True), html.escape(commit)
    page = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>File-open instructions benchmark</title><style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f4f7fa}}body{{max-width:1000px;margin:3rem auto;padding:0 1rem}}
.note{{padding:1rem;background:#e8f1fa;border-radius:.5rem}}table{{border-collapse:collapse;width:100%;background:white;margin:1.5rem 0}}
th,td{{padding:.7rem;border-bottom:1px solid #d7e0e8;text-align:left}}th{{background:#e8f1fa}}a{{color:#0759a5}}
svg{{width:100%;max-width:900px;background:white}}rect{{fill:#2477ae}}text{{font:14px system-ui,sans-serif;fill:#17212b}}
</style></head><body><main><h1>File-open instructions benchmark</h1>
<p>Latest complete run: <a href='{safe_run}'>GitHub Actions</a> · commit <code>{safe_commit}</code></p>
<p class='note'>The benchmark opens a prepared <code>hello.txt</code> containing exactly <code>hello world</code>. It starts with a fresh editor profile and waits for the editor window before counting. The measured interval begins before the normal open command (Ctrl+O in VS Code, Geany and minimal Electron; File &gt; Open File in LVCE), includes the file chooser and open request, and ends after a fixed one-second response window. Editor descendants are counted. Fixture preparation, launch, readiness, focus, the external input driver, document verification and cleanup are excluded. Each of three trials uses a fresh profile. The report shows medians, raw counters, host identity and protocol details.</p>
<p>Minimal Electron uses LVCE's exact packaged Electron runtime with only its application replaced by a native open dialog and textarea. It uses Ctrl+O; LVCE uses its custom menu with a 150ms menu pause. This reference omits LVCE's editor features and does not isolate the cost of workers or virtual DOM.</p>
<p>The Linux ARM64 comparison includes all required editors. All {len(entries)} completed with valid measurements.</p>
<svg viewBox='0 0 {chart_width} 350' role='img' aria-label='Median retired instructions to open hello.txt'>{''.join(bars)}</svg>
<table><thead><tr><th>Editor</th><th>Version</th><th>Median instructions to open file</th><th>Trials</th><th>Raw data</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><p><a href='report.json'>Download complete report</a></p></main></body></html>"""
    (output / 'index.html').write_text(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'results/editors')
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/pages')
    parser.add_argument('--run-url', default='')
    parser.add_argument('--commit', default='')
    args = parser.parse_args()
    results = load_results(args.input)
    build_report(results, args.output, args.run_url, args.commit)
    print(f'Built a complete report for {len(results)} editors at {args.output}')


if __name__ == '__main__':
    main()
