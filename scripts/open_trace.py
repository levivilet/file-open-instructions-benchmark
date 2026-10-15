"""Diagnostic Chromium/V8 trace, including dedicated workers.

Only used by profile_open.py, never by the uninstrumented comparison.
"""
import base64
import json

from cdp import Page


class OpenTrace:
    def __init__(self, port):
        self.page = Page(port, timeout=5)
        try:
            self.page.call('Tracing.start',
                           categories='v8,devtools.timeline,disabled-by-default-v8.cpu_profiler,disabled-by-default-v8.cpu_profiler.hires',
                           transferMode='ReturnAsStream')
        except BaseException:
            self.page.close()
            raise

    def finish(self, destination):
        try:
            # tracingComplete can arrive before the command response, so do
            # not use Page.call (which intentionally ignores unrelated events).
            self.page.counter += 1
            request_id = self.page.counter
            self.page.socket.send(json.dumps({'id': request_id, 'method': 'Tracing.end'}))
            while True:
                message = json.loads(self.page.socket.recv())
                if message.get('id') == request_id and 'error' in message:
                    raise RuntimeError(message['error'])
                if message.get('method') == 'Tracing.tracingComplete':
                    if message['params'].get('dataLossOccurred'):
                        raise RuntimeError('Chromium trace lost events')
                    handle = message['params']['stream']
                    break
            with destination.open('wb') as output:
                while True:
                    chunk = self.page.call('IO.read', handle=handle, size=1024 * 1024)
                    data = chunk.get('data', '')
                    output.write(base64.b64decode(data) if chunk.get('base64Encoded') else data.encode())
                    if chunk.get('eof'):
                        break
            self.page.call('IO.close', handle=handle)
        finally:
            self.page.close()

    def close(self):
        self.page.close()
