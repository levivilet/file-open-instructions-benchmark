"""Unmodified official-release comparison for the selection-restoration lifecycle changes."""
import json
from pathlib import Path

import benchmark
import compare_open

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / 'config/shipped-render-baseline.json'


def validate_experiment(data, run_url=None, commit=None):
    import report
    if 'experimentalBundlePatch' in data:
        raise ValueError('shipped comparison must use unmodified official releases')
    report.validate_comparison(data, benchmark.PROTOCOL, baseline=json.loads(BASELINE.read_text()))
    if run_url is not None and (data['host'].get('runUrl') != run_url or data['host'].get('commit') != commit):
        raise ValueError('shipped comparison belongs to a different workflow run')
    return data


if __name__ == '__main__':
    compare_open.main(baseline_path=BASELINE, destination=Path('results/shipped-render-comparison.json'),
                      baseline_directory='lvce-shipped-render-baseline')
