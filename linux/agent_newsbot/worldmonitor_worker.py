"""Single bounded HTTP operation. Parent enforces total wall time and reaps this process."""
import base64
import json
import sys
import requests

MAX_BYTES=4*1024*1024

class SizeLimitError(Exception): pass


def main():
    request=json.loads(sys.stdin.buffer.read(65537))
    headers={'Accept':'application/json'}
    if request.get('key'): headers['X-WorldMonitor-Key']=request['key']
    session = requests.Session()
    session.trust_env = False  # Do not forward credentials through inherited environment proxies.
    with session, session.get(request['url'],params={'variant':request['variant'],'lang':'en'},headers=headers,
                      timeout=(5,15),allow_redirects=False,stream=True) as response:
        if response.status_code!=200: return {'status':response.status_code}
        body=bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body)>MAX_BYTES: raise SizeLimitError()
        return {'status':200,'body':base64.b64encode(body).decode('ascii')}


if __name__=='__main__':
    try: result=main()
    except requests.exceptions.SSLError: result={'error':'tls'}
    except requests.exceptions.Timeout: result={'error':'timeout'}
    except SizeLimitError: result={'error':'response_too_large'}
    except Exception: result={'error':'network'}  # Never echo credential-bearing URLs, headers or response bodies.
    sys.stdout.write(json.dumps(result))
