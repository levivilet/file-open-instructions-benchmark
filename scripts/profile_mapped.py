"""Mapped native diagnostic only; V8 flags perturb execution, never report input.

See https://v8.dev/docs/linux-perf and https://nodejs.org/learn/diagnostics/poor-performance/using-linux-perf.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import profile_native
from profile_open import ProfiledCapture


class MappedCapture(ProfiledCapture):
    def __enter__(self):
        self.map_links = []
        self.maps_ready = False
        return super().__enter__()

    def launch(self, command, environment, *args, **kwargs):
        self.map_directory = self.directory / 'v8-perf-maps'
        self.map_directory.mkdir()
        if 'SUDO_UID' in environment:
            os.chown(self.map_directory, int(environment['SUDO_UID']), int(environment['SUDO_GID']))
        command = [*command, '--js-flags=--perf-basic-prof --interpreted-frames-native-stack '
                   f'--perf-basic-prof-path={self.map_directory}']
        self.diagnostic_command = command
        return super().launch(command, environment, *args, **kwargs)

    def snapshot(self, name):
        super().snapshot(name)
        if name != 'after':
            return
        owned_pids = set(self.pids())
        maps = list(self.map_directory.glob('perf-*.map'))
        # Native perf resolves JIT ranges from /tmp/perf-PID.map. Keep actual V8
        # output private and expose only these live cgroup processes' maps.
        for source in maps:
            match = re.fullmatch(r'perf-(\d+)\.map', source.name)
            if not match:
                raise ValueError('invalid V8 map name')
            if int(match[1]) not in owned_pids:
                continue  # exited processes cannot contribute after cutoff
            destination = Path('/tmp') / source.name
            destination.symlink_to(source)  # fail closed on any existing path
            self.map_links.append((destination, source))
        if not self.map_links:
            raise ValueError('V8 did not produce native symbol maps')
        self.maps_ready = True

    def cutoff(self):
        super().cutoff()
        if not self.maps_ready:
            return
        destination = self.output / 'v8-perf-maps'
        destination.mkdir()
        receipt = []
        editor_symbols = []
        for _, source in self.map_links:
            data = source.read_bytes()
            shutil.copyfile(source, destination / source.name)
            receipt.append({'file': source.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            editor_symbols.extend(line for line in data.decode().splitlines()
                                  if 'JS:' in line and '/packages/editor-worker/' in line)
        (self.output / 'mapped-symbols.json').write_text(json.dumps({
            'diagnosticFlags': ['--perf-basic-prof', '--interpreted-frames-native-stack'],
            'maps': receipt, 'editorSymbolCount': len(editor_symbols),
            'editorSymbolExamples': editor_symbols[:12],
        }, indent=2) + '\n')
        if not editor_symbols:
            raise ValueError('V8 maps lack positive editor-worker JavaScript symbols')
        with (self.output / 'mapped-events.txt').open('w') as output:
            subprocess.run([self.perf, 'script', '-G', '--no-inline', '-i', str(self.output / 'perf.data'),
                            '-F', 'comm,pid,tid,time,event,ip,sym,dso'], stdout=output, check=True)

    def finish(self):
        result = super().finish()
        result['diagnosticCommand'] = self.diagnostic_command
        result['diagnosticOnly'] = True
        return result

    def __exit__(self, *args):
        try:
            super().__exit__(*args)
        finally:
            for destination, source in self.map_links:
                if destination.is_symlink() and destination.readlink() == source:
                    destination.unlink()


def main():
    original = profile_native.profile_open.ProfiledCapture
    profile_native.profile_open.ProfiledCapture = MappedCapture
    try:
        profile_native.main()
    finally:
        profile_native.profile_open.ProfiledCapture = original


if __name__ == '__main__':
    main()
