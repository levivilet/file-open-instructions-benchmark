"""Diagnostic instruction sampling around the unchanged LVCE open protocol.

Sampling perturbs execution. These results are deliberately separate from the
published benchmark; use the ordinary workflow to evaluate any optimization.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

import benchmark
from counters import Capture


class ProfiledCapture(Capture):
    def __enter__(self):
        self.profile = None
        self.profile_log = None
        self.output = Path('results/profiles') / uuid.uuid4().hex
        self.output.mkdir(parents=True)
        super().__enter__()
        return self

    def start(self):
        # Capture has already launched/settled the editor. Acknowledge the
        # sampler before enabling the ordinary hardware counter so that no
        # opening work is missed. Stop sampling only after counter cutoff.
        for name in ['profile-control', 'profile-ack']:
            path = self.directory / name
            os.mkfifo(path)
            self.fds.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
        self.profile_log = (self.output / 'record.log').open('w')
        self.profile = subprocess.Popen([
            self.perf, 'record', '-a', '-e', 'instructions', '-c', '1000000',
            '-G', self.group.name, '--call-graph', 'fp', '--timestamp',
            '-o', str(self.output / 'perf.data'), '-D', '-1',
            '--control', f'fd:{self.fds[-2]},{self.fds[-1]}',
        ], pass_fds=tuple(self.fds[-2:]), stdout=subprocess.DEVNULL,
            stderr=self.profile_log, start_new_session=True)
        # Reuse the acknowledged control protocol without touching the stat
        # controller's descriptor order or child-process ownership.
        self.profile_control('enable')
        self.sample_started = time.monotonic()
        self.snapshot('before')
        super().start()
        self.counter_started = time.monotonic()

    def profile_control(self, command):
        import select
        os.write(self.fds[-2], (command + '\n').encode())
        if not select.select([self.fds[-1]], [], [], 5)[0]:
            raise RuntimeError(f'sampling perf did not acknowledge {command}')
        if os.read(self.fds[-1], 256) not in (b'ack\n', b'ack\n\x00'):
            raise RuntimeError('invalid sampling acknowledgement')
        if self.profile.poll() is not None:
            raise RuntimeError('sampling perf exited')

    def snapshot(self, name):
        rows = []
        for pid in self.pids():
            try:
                rows.append({'pid': pid,
                             'command': Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')})
            except FileNotFoundError:
                pass
        (self.output / f'processes-{name}.json').write_text(json.dumps(rows, indent=2))

    def cutoff(self):
        super().cutoff()
        if self.profile and self.profile.poll() is None:
            self.profile_control('disable')
            self.sample_stopped = time.monotonic()
            self.snapshot('after')
            self.profile.send_signal(signal.SIGINT)
            self.profile.wait(timeout=10)
            self.profile_log.flush()
            (self.output / 'boundaries.json').write_text(json.dumps({
                'samplingStart': self.sample_started, 'counterStart': self.counter_started,
                'counterCutoff': self.disabled_at, 'samplingCutoff': self.sample_stopped,
                'note': 'diagnostic only; sampler encloses normal counter window',
            }, indent=2))
            # Resolve native symbols while the editor artifacts are installed.
            for sort, name in [('pid,comm,dso,symbol', 'symbols'), ('pid,comm', 'threads')]:
                with (self.output / f'{name}.txt').open('w') as report:
                    subprocess.run([self.perf, 'report', '--stdio', '--no-children',
                                    '-i', str(self.output / 'perf.data'), '--sort', sort,
                                    '--percent-limit', '0.5'], stdout=report, check=True)

    def __exit__(self, *args):
        try:
            super().__exit__(*args)
        finally:
            if self.profile and self.profile.poll() is None:
                self.profile.kill()
                self.profile.wait()
            if self.profile_log:
                self.profile_log.close()
            # perf runs as root for cgroup counters and creates a mode-0600
            # recording. The unprivileged artifact uploader must read it.
            data = self.output / 'perf.data'
            if data.exists():
                data.chmod(0o644)


if __name__ == '__main__':
    benchmark.Capture = ProfiledCapture
    # The runner passes normal --editor/--perf/--repeats options. Output stays
    # in a diagnostic job artifact, never an open-* published-report input.
    benchmark.main()
