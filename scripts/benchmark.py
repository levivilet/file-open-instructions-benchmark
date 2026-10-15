"""Measure retired CPU instructions for opening and verifying a text file."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import uuid

from counters import Capture

ROOT = Path(__file__).resolve().parent.parent
CONTENT = 'hello world'
RESPONSE_WINDOW_SECONDS = 1.0
PROTOCOL = {
    'version': 1,
    'metric': 'retired hardware instructions (user and kernel)',
    'action': 'open the prepared hello.txt fixture; VS Code uses Ctrl+O and LVCE uses File > Open File; both enter its absolute path with Ctrl+L',
    'boundary': 'acknowledged perf enable before the open command through acknowledged disable 1.0 seconds after the file-open Return',
    'responseWindowSeconds': RESPONSE_WINDOW_SECONDS,
    'accounting': 'per-CPU cgroup; includes editor descendants and all work during the open response window',
    'excluded': 'fixture creation, launch, window readiness, settling, focus, xdotool, clipboard and title verification, cleanup',
    'profile': 'fresh HOME and XDG directories for every trial',
    'cache': 'warm OS filesystem caches permitted; application profiles always fresh',
    'launcher': 'editor cgroup contains editor process tree; controller, X server and desktop stay outside',
    'cutoff': 'perf disable acknowledgement after fixed response window; control latency through acknowledgement is included',
    'verification': 'after cutoff, title must name hello.txt and select-all/copy must return exactly hello world',
    'repeats': 3,
}


def command_for(editor, home):
    binary = str(ROOT / '.tmp/apps' / editor['id'] / editor['binary'])
    args = ['--no-sandbox', '--disable-gpu', '--ozone-platform=x11',
            '--user-data-dir', str(home / 'profile'), '--disable-extensions',
            '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust']
    if editor['id'] == 'lvce':
        args = ['--no-sandbox', '--disable-gpu', '--ozone-platform=x11',
                '--user-data-dir', str(home / 'profile')]
    return [binary, *args]


def is_main_window(properties):
    lines = properties.splitlines()
    type_line = next((line for line in lines if line.startswith('_NET_WM_WINDOW_TYPE')), '')
    transient_line = next((line for line in lines if line.startswith('WM_TRANSIENT_FOR')), '')
    managed = 'window state: Normal' in properties
    normal = '_NET_WM_WINDOW_TYPE_NORMAL' in type_line
    default_normal = (type_line.partition(':')[2].strip() == 'not found.' and
                      transient_line.partition(':')[2].strip() == 'not found.')
    return managed and (normal or default_normal)


def visible_window(pids):
    for pid in pids:
        search = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(pid)],
                                capture_output=True, text=True)
        for window in search.stdout.split():
            props = subprocess.check_output(['xprop', '-id', window, '_NET_WM_WINDOW_TYPE',
                                             'WM_NAME', 'WM_CLASS', '_NET_WM_PID',
                                             'WM_TRANSIENT_FOR', 'WM_STATE'], text=True)
            if is_main_window(props):
                return {'windowId': window, 'pid': pid, 'properties': props}
    return None


def wait_for_open_dialog(main_window, details, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        focused = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
        if focused.lower() != main_window.lower():
            details['openDialogTitle'] = subprocess.check_output(['xdotool', 'getwindowname', focused], text=True).strip()
            return focused
        time.sleep(.02)
    details['focusAfterOpenCommand'] = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
    details['titleAfterOpenCommand'] = window_title(main_window)
    raise RuntimeError('open-file dialog did not take focus')


def trigger_open(editor_id, window_id, details):
    if editor_id == 'lvce':
        # LVCE exposes Open File in its custom File menu, without a Ctrl+O binding.
        subprocess.run(['xdotool', 'mousemove', '--window', window_id, '22', '16', 'click', '1'], check=True)
        time.sleep(.15)
        details['lvceMenuFocus'] = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
        details['lvceTitleAfterFileClick'] = window_title(window_id)
        subprocess.run(['xdotool', 'mousemove', '--window', window_id, '80', '104', 'click', '1'], check=True)
    else:
        subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+o'], check=True)
    return wait_for_open_dialog(window_id, details)


def window_title(window_id):
    return subprocess.check_output(['xdotool', 'getwindowname', window_id], text=True).strip()


def trial(editor, perf):
    with tempfile.TemporaryDirectory(prefix='file-open-trial-') as directory:
        d = Path(directory)
        home = d / 'home'
        home.mkdir()
        fixture = d / 'hello.txt'
        fixture.write_text(CONTENT)
        env = {**os.environ, 'HOME': str(home), 'LC_ALL': 'C',
               'LIBGL_ALWAYS_SOFTWARE': '1', 'GALLIUM_DRIVER': 'llvmpipe',
               'ELECTRON_OZONE_PLATFORM_HINT': 'x11'}
        for key, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                          ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                          ('XDG_RUNTIME_DIR', 'runtime')]:
            env[key] = str(home / name)
            (home / name).mkdir()
        (home / 'runtime').chmod(0o700)
        if 'SUDO_UID' in env:
            uid, gid = int(env['SUDO_UID']), int(env['SUDO_GID'])
            for path in [d, home, fixture, *home.iterdir()]:
                os.chown(path, uid, gid)

        command = command_for(editor, home)
        details = {'stage': 'launch', 'command': command, 'fixture': str(fixture)}
        failure_diagnostics = {}
        try:
            with Capture(perf, d) as capture:
                # Launch, first-run UI, readiness and settling are outside capture.
                capture.cutoff()
                with (d / 'stdout').open('w') as stdout, (d / 'stderr').open('w') as stderr:
                    child = capture.launch(command, env, stdout, stderr, cwd=d)
                    start = time.monotonic()
                    while True:
                        pids = capture.pids()
                        if not pids:
                            raise RuntimeError(f'editor exited before main window: {child.poll()}')
                        window = visible_window(pids)
                        if window:
                            break
                        if time.monotonic() - start > 120:
                            failure_diagnostics = {'reason': 'main window readiness timeout'}
                            raise RuntimeError('main window readiness timeout')
                        time.sleep(.02)
                    time.sleep(10)
                    window_id = window['windowId']
                    subprocess.run(['xdotool', 'windowactivate', '--sync', window_id], check=True)
                    subprocess.run(['xdotool', 'mousemove', '--window', window_id, '500', '400'], check=True)
                    subprocess.run(['xdotool', 'click', '1'], check=True)
                    focus = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
                    focus_pid = int(subprocess.check_output(['xdotool', 'getwindowpid', focus], text=True).strip())
                    pids = capture.pids()
                    if focus_pid not in pids:
                        raise RuntimeError(f'focus is outside editor cgroup: window={focus}, pid={focus_pid}')
                    time.sleep(.5)
                    capture.start()
                    measured_started = time.monotonic()
                    details['stage'] = 'open-dialog'
                    details['openDialog'] = trigger_open(editor['id'], window_id, details)
                    details['stage'] = 'enter-path'
                    subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+l'], check=True)
                    details['locationEntryFocus'] = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
                    details['locationEntryTitle'] = subprocess.check_output(
                        ['xdotool', 'getwindowname', details['locationEntryFocus']], text=True).strip()
                    subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '0', '--', str(fixture)], check=True)
                    subprocess.run(['xdotool', 'key', '--clearmodifiers', 'Return'], check=True)
                    # GTK's location entry can accept the path and return to the
                    # chooser before its Open action. Submit again only if the
                    # document title has not changed after the first Return.
                    title_deadline = time.monotonic() + .5
                    while fixture.name not in window_title(window_id) and time.monotonic() < title_deadline:
                        time.sleep(.02)
                    details['titleAfterFirstReturn'] = window_title(window_id)
                    if fixture.name not in details['titleAfterFirstReturn']:
                        details['secondReturnFocus'] = subprocess.check_output(['xdotool', 'getwindowfocus'], text=True).strip()
                        subprocess.run(['xdotool', 'key', '--clearmodifiers', 'Return'], check=True)
                    details['titleAfterOpenSubmit'] = window_title(window_id)
                    opened_at = time.monotonic()
                    remaining = opened_at + RESPONSE_WINDOW_SECONDS - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    capture.cutoff()
                    result = capture.finish()
                    result.update(valid=True, readiness=window, fixture=str(fixture),
                                  focusWindow=focus, focusPid=focus_pid, pidsAtOpen=pids,
                                  openDispatchSeconds=opened_at-measured_started,
                                  openToCutoffSeconds=capture.disabled_at-opened_at,
                                  measurementSeconds=capture.disabled_at-measured_started,
                                  command=command)
                    details['stage'] = 'verify-document'
                    title = window_title(window_id)
                    if fixture.name not in title:
                        raise RuntimeError(f'editor title does not identify opened fixture: {title!r}')
                    subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'], check=True)
                    subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+c'], check=True)
                    copied = subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], text=True,
                                                     timeout=5)
                    details.update(windowTitle=title, copiedContent=copied)
                    if copied != CONTENT:
                        raise RuntimeError(f'opened document content mismatch: expected {CONTENT!r}, got {copied!r}')
                    result.update(documentLoaded=True, documentContent=copied, windowTitle=title)
            child.wait(timeout=5)
            return result
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            snapshot = d / 'desktop.xwd'
            snapshot_result = subprocess.run(['xwd', '-silent', '-root', '-out', str(snapshot)],
                                             capture_output=True, text=True)
            if snapshot_result.returncode == 0 and snapshot.exists():
                diagnostics = Path('results/diagnostics')
                diagnostics.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot, diagnostics / f"{editor['id']}-{uuid.uuid4().hex}.xwd")
            return {'valid': False, 'error': str(error), 'command': command,
                    'diagnostics': {**details, **failure_diagnostics},
                    **{name: (d / name).read_text(errors='replace')[-8000:]
                       for name in ['stdout', 'stderr', 'perf.log'] if (d / name).exists()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', required=True)
    parser.add_argument('--perf', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error('at least three trials required')
    editor = next(e for e in json.loads((ROOT / 'config/editors.lock.json').read_text()) if e['id'] == args.editor)
    host = {'machine': platform.machine(), 'kernel': platform.release(),
            'cpuinfo': Path('/proc/cpuinfo').read_text(),
            'perf': subprocess.check_output([args.perf, '--version'], text=True).strip(),
            'eventSources': sorted(p.name for p in Path('/sys/bus/event_source/devices').iterdir()),
            'paranoid': Path('/proc/sys/kernel/perf_event_paranoid').read_text().strip(),
            'editorUid': int(os.environ.get('SUDO_UID', os.getuid())),
            'runUrl': os.environ.get('BENCHMARK_RUN_URL'), 'commit': os.environ.get('BENCHMARK_COMMIT')}
    if host['machine'] != 'aarch64':
        raise SystemExit('requires Linux ARM64')
    trials = [dict(trial(editor, args.perf), repeat=i + 1) for i in range(args.repeats)]
    Path('results').mkdir(exist_ok=True)
    Path('results/results.json').write_text(json.dumps(
        dict(editor=editor, protocol=PROTOCOL, host=host, trials=trials), indent=2) + '\n')
    if not all(t['valid'] for t in trials):
        raise SystemExit('invalid trials; cannot publish complete report')


if __name__ == '__main__':
    main()
