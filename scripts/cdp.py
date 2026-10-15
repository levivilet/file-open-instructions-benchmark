"""Read-only CDP client for post-cutoff document identity verification."""
import json
import time
import urllib.request


class Page:
    def __init__(self, port, timeout=60, url_suffix=None):
        import websocket

        deadline = time.monotonic() + timeout
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2) as response:
                    targets = json.load(response)
                target = next(t for t in targets if t['type'] == 'page' and not t['url'].startswith('devtools:')
                              and (url_suffix is None or t['url'].split('?')[0].endswith(url_suffix)))
                self.socket = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=5, suppress_origin=True)
                self.counter = 0
                return
            except (OSError, StopIteration):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Editor never exposed a page')
                time.sleep(.25)

    def call(self, method, **params):
        self.counter += 1
        self.socket.send(json.dumps({'id': self.counter, 'method': method, 'params': params}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get('id') == self.counter:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message['result']

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True)
        if result.get('exceptionDetails'):
            raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def close(self):
        self.socket.close()
