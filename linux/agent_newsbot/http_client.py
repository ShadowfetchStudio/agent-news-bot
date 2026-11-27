"""Bounded direct-provider transfers, including DNS, redirects and slow streams."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys

from requests.structures import CaseInsensitiveDict

MAX_BYTES = 4 * 1024 * 1024


class RequestFailure(RuntimeError):
    def __init__(self, code, status=None, retry_after=None):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(f'Provider HTTP {status}' if status else f'Provider request failed: {code}')


class Response:
    def __init__(self, status, headers, content):
        self.status_code = status
        self.headers = CaseInsensitiveDict(headers)
        self.content = content

    def raise_for_status(self):
        if not (200 <= self.status_code < 300 or self.status_code == 304):
            raise RequestFailure('http', self.status_code, self.headers.get('Retry-After'))

    def json(self):
        self.raise_for_status()
        return json.loads(self.content)


def get(url, *, headers=None, timeout=14):
    from .source_health import CURRENT
    attempt = CURRENT.get()
    if attempt: attempt.check()
    try: return _get(url, headers=headers, timeout=timeout)
    except RequestFailure as error:
        if attempt: attempt.record(f"http_{error.status}" if error.status else error.code, error.retry_after)
        raise


def _get(url, *, headers=None, timeout=14):
    if not 0 < timeout <= 120:
        raise ValueError('HTTP deadline must be greater than zero and at most 120 seconds')
    job = json.dumps({'url': url, 'headers': headers or {}, 'timeout': timeout}).encode()
    if len(job) > 65536:
        raise ValueError('HTTP request configuration exceeds 64 KiB')
    try:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('http_worker.py'))],
                                input=job, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for its owned child before raising.
        raise RequestFailure('total_timeout') from None
    if result.returncode or len(result.stdout) > MAX_BYTES * 2:
        raise RequestFailure('worker')
    value = json.loads(result.stdout)
    if value.get('error'):
        raise RequestFailure(value['error'])
    content = base64.b64decode(value['body'], validate=True)
    if len(content) > MAX_BYTES:
        raise RequestFailure('too_large')
    response = Response(value['status'], value['headers'], content)
    response.raise_for_status()
    return response
