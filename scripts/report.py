"""Build a static, validated file-open instruction comparison."""
import argparse
import html
import importlib
import json
import math
from pathlib import Path
import statistics

from benchmark import CONTENT, RESPONSE_WINDOW_SECONDS
from counters import parse_counter
from compare_open import summarize

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
                or (editor_id in {'lvce', 'electron', 'zed'} and (not trial.get('fixture', '').endswith('/hello.txt')
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


def validate_comparison(data, protocol):
    baseline = json.loads((ROOT / 'config/lvce-baseline.json').read_text())
    candidate = next(editor for editor in EDITORS if editor['id'] == 'lvce')
    if (data.get('baseline') != baseline or data.get('candidate') != candidate
            or data.get('protocol') != protocol or data.get('host', {}).get('machine') != 'aarch64'):
        raise ValueError('paired comparison identity, protocol or architecture mismatch')
    pairs = data.get('pairs', [])
    if len(pairs) != 40:
        raise ValueError('paired comparison requires all forty pairs')
    for index, pair in enumerate(pairs):
        order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
        if pair.get('index') != index or pair.get('order') != order:
            raise ValueError('paired comparison must preserve all alternating-order pairs')
    for variant in ['baseline', 'candidate']:
        validate_result({'editor': data[variant], 'host': data['host'], 'protocol': protocol,
                         'trials': [pair[variant] for pair in pairs]}, 'lvce')
    if data.get('summary') != summarize(pairs):
        raise ValueError('paired summary does not match raw trials')
    return data


EXPERIMENTS = [
    ('preferences', 'Preference batching'), ('dialog', 'Chooser configuration'),
    ('read', 'Renderer content read'), ('tab', 'First-tab scrolling'),
    ('minify', 'Worker minification'), ('compact', 'Alternative name-preserving minifier'),
    ('lazy', 'Lazy editor commands'), ('runtime', 'Electron runtime'),
    ('combined', 'Combined runtime and minification'), ('font', 'Bundled font'),
    ('render', 'Initial render'), ('plain', 'Plaintext tokenizer'),
    ('cache', 'V8 cache policy'), ('summary', 'Local Problems summary'),
    ('response', 'Buffered JavaScript response'), ('diff', 'Redundant diff RPC'),
]


def load_experiments(directory, run_url, commit):
    results = []
    for key, label in EXPERIMENTS:
        data = json.loads((directory / f'paired-{key}/{key}-comparison.json').read_text())
        importlib.import_module(f'compare_{key}').validate_experiment(data)
        if data['host'].get('runUrl') != run_url or data['host'].get('commit') != commit:
            raise ValueError(f'{key}: controlled experiment belongs to a different workflow run')
        results.append((key, label, data))
    return results


def build_report(results, output, run_url, commit, comparison=None, ipc_comparison=None, experiments=None):
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
    comparison_html = ''
    if comparison is not None:
        (raw_dir / 'paired-lvce.json').write_text(json.dumps(comparison, indent=2) + '\n')
        summary = comparison['summary']
        low, high = summary['bootstrap95MeanSavings']
        comparison_html = (f"<h2>Paired official LVCE releases</h2><p>{html.escape(comparison['baseline']['version'])} to "
                           f"{html.escape(comparison['candidate']['version'])}: mean instructions saved per open "
                           f"{summary['meanPairedInstructionsSaved'] / 1e6:.2f}M; bootstrap 95% interval "
                           f"[{low / 1e6:.2f}M, {high / 1e6:.2f}M]. Positive values favor the candidate. "
                           f"All {len(comparison['pairs'])} alternating-order pairs are retained. "
                           "This compares complete releases and includes any intervening changes. "
                           "<a href='raw/paired-lvce.json'>Download every pair and artifact identity</a>.</p>")
    ipc_html = ''
    if ipc_comparison is not None:
        (raw_dir / 'paired-ipc.json').write_text(json.dumps(ipc_comparison, indent=2) + '\n')
        summary = ipc_comparison['summary']
        low, high = summary['bootstrap95MeanSavings']
        ipc_html = (f"<h2>Controlled view-discovery IPC experiment</h2><p>Two copies of official "
                    f"{html.escape(ipc_comparison['baseline']['version'])}, with only the recorded renderer-worker "
                    "view-discovery function changed: skip the unused extension-inventory request when every view has CSS. "
                    f"Mean instructions saved per open {summary['meanPairedInstructionsSaved'] / 1e6:.2f}M; "
                    f"bootstrap 95% interval [{low / 1e6:.2f}M, {high / 1e6:.2f}M]. "
                    "All forty uninstrumented alternating-order pairs are retained. This diagnostic uses a patched bundle; "
                    "the official-release comparison above validates shipped packages. "
                    "<a href='raw/paired-ipc.json'>Download every controlled pair and exact patch</a>. "
                    "<a href='ipc-analysis.html'>Read the process/worker message-flow analysis</a>.</p>")
    experiment_entries, experiment_rows = [], []
    for key, label, data in experiments or []:
        filename = f'paired-{key}.json'
        (raw_dir / filename).write_text(json.dumps(data, indent=2) + '\n')
        summary = data['summary']
        low, high = summary['bootstrap95MeanSavings']
        entry = {'id': key, 'name': label, 'baseline': data['baseline'], 'candidate': data['candidate'],
                 'summary': summary, 'raw': f'raw/{filename}'}
        experiment_entries.append(entry)
        experiment_rows.append(f"<tr><th>{html.escape(label)}</th><td>{html.escape(data['baseline']['version'])}</td>"
                               f"<td>{summary['meanPairedInstructionsSaved'] / 1e6:.2f}M</td>"
                               f"<td>[{low / 1e6:.2f}M, {high / 1e6:.2f}M]</td>"
                               f"<td><a href='raw/{filename}'>Every pair and exact transformation</a></td></tr>")
    experiments_html = ''
    if experiment_entries:
        experiments_html = ("<h2>Controlled diagnostic experiments</h2>"
                            "<p>Each row retains forty uninstrumented pairs on its own frozen release and configuration. "
                            "Patched diagnostic bundles are separate from the official release results above. "
                            "Positive savings favor the candidate; intervals spanning zero are inconclusive. "
                            "Isolated savings must not be added together or pooled across runs.</p>"
                            "<table><thead><tr><th>Experiment</th><th>Frozen release</th><th>Mean saved</th>"
                            "<th>95% interval</th><th>Raw data</th></tr></thead><tbody>" + ''.join(experiment_rows) + '</tbody></table>')
    target, target_html = None, ''
    lvce = next((entry for entry in entries if entry['id'] == 'lvce'), None)
    if lvce:
        measured = comparison['summary']['candidateMedian'] if comparison else lvce['medianInstructions']
        target = {'instructions': 1_300_000_000, 'candidateMedian': measured, 'met': measured < 1_300_000_000,
                  'statistic': 'forty-pair candidate median' if comparison else 'three-trial median'}
        status = 'met' if target['met'] else 'unmet'
        target_html = (f"<p class='note'>LVCE target: below 1,300M instructions. The {target['statistic']} is "
                       f"{measured / 1e6:.2f}M; target {status}.</p>")
    report = {'complete': True, 'runUrl': run_url, 'commit': commit, 'editors': entries, 'pairedComparison': comparison, 'controlledIpcComparison': ipc_comparison, 'controlledExperiments': experiment_entries, 'lvceTarget': target}
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
<p class='note'>The benchmark opens a prepared <code>hello.txt</code> containing exactly <code>hello world</code>. It starts with a fresh editor profile and waits for the editor window before counting. The measured interval begins before the normal open command (Ctrl+O in VS Code, Geany, Zed and minimal Electron; File &gt; Open File in LVCE), includes the file chooser, a fixed 100ms location-entry settling pause after Ctrl+L, and open request, and ends after a fixed one-second response window. Editor descendants are counted. Fixture preparation, launch, readiness, focus, the external input driver, Zed’s portal file chooser, document verification and cleanup are excluded. Each of three trials uses a fresh profile. The report shows medians, raw counters, host identity and protocol details.</p>
<p>Minimal Electron uses LVCE's exact packaged Electron runtime with only its application replaced by a native open dialog and textarea. It uses Ctrl+O; LVCE uses its custom menu with a 150ms menu pause. This reference omits LVCE's editor features and does not isolate the cost of workers or virtual DOM.</p>
<p>The Linux ARM64 comparison includes all required editors. All {len(entries)} completed with valid measurements.</p>
{target_html}<svg viewBox='0 0 {chart_width} 350' role='img' aria-label='Median retired instructions to open hello.txt'>{''.join(bars)}</svg>
<table><thead><tr><th>Editor</th><th>Version</th><th>Median instructions to open file</th><th>Trials</th><th>Raw data</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>{comparison_html}{ipc_html}{experiments_html}<p><a href='report.json'>Download complete report</a></p></main></body></html>"""
    (output / 'index.html').write_text(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'results/editors')
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/pages')
    parser.add_argument('--run-url', default='')
    parser.add_argument('--commit', default='')
    parser.add_argument('--experiments', type=Path)
    args = parser.parse_args()
    results = load_results(args.input)
    comparison = validate_comparison(json.loads((ROOT / 'results/paired/comparison.json').read_text()),
                                     results[0]['data']['protocol'])
    if comparison['host'].get('commit') != args.commit or comparison['host'].get('runUrl') != args.run_url:
        raise ValueError('paired comparison belongs to a different workflow run')
    # Import after report helpers are defined; the experiment reuses trial validation.
    from compare_ipc import validate_experiment
    ipc_comparison = validate_experiment(json.loads((ROOT / 'results/ipc/ipc-comparison.json').read_text()))
    if ipc_comparison['host'].get('commit') != args.commit or ipc_comparison['host'].get('runUrl') != args.run_url:
        raise ValueError('controlled IPC comparison belongs to a different workflow run')
    experiments = load_experiments(args.experiments, args.run_url, args.commit) if args.experiments else []
    build_report(results, args.output, args.run_url, args.commit, comparison, ipc_comparison, experiments)
    (args.output / 'ipc-analysis.html').write_text((ROOT / 'docs/ipc-analysis.html').read_text())
    print(f'Built a complete report for {len(results)} editors at {args.output}')


if __name__ == '__main__':
    main()
