"""One HTTP transfer; its parent owns the absolute deadline and reaps it."""
import base64
import json
import sys
from urllib.parse import urljoin, urlsplit

import requests

MAX_BYTES = 4 * 1024 * 1024


def main():
    job = json.loads(sys.stdin.buffer.read(65537))
    url = job['url']
    for hop in range(6):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return {'error': 'invalid_url'}
        with requests.get(url, headers=job['headers'], stream=True, allow_redirects=False,
                          timeout=(min(5, job['timeout']), job['timeout'])) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get('Location', ''))
                if hop == 5 or not response.headers.get('Location') or (parts.scheme == 'https' and urlsplit(target).scheme != 'https'):
                    return {'error': 'redirect'}
                url = target
                continue
            headers = {name: response.headers[name] for name in
                       ('ETag', 'Last-Modified', 'Content-Type', 'Retry-After') if name in response.headers}
            result = {'status': response.status_code, 'headers': headers, 'body': ''}
            if not 200 <= response.status_code < 300:
                return result
            data = bytearray()
            for chunk in response.iter_content(65536):
                if len(data) + len(chunk) > MAX_BYTES:
                    return {'error': 'too_large'}
                data.extend(chunk)
            result['body'] = base64.b64encode(data).decode('ascii')
            return result


if __name__ == '__main__':
    try:
        result = main()
    except requests.exceptions.SSLError:
        result = {'error': 'tls'}
    except requests.exceptions.Timeout:
        result = {'error': 'timeout'}
    except requests.exceptions.ConnectionError:
        result = {'error': 'connection'}
    except Exception:
        result = {'error': 'request'}
    # Never return exception strings, credentials, URLs or response error bodies.
    sys.stdout.write(json.dumps(result))
