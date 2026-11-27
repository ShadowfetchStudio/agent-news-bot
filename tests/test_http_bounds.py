"""Real-socket regression tests for malformed and slow bridge clients."""
import contextlib
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux'))
from agent_newsbot import server


@contextlib.contextmanager
def bridge(handler=server.Handler):
    class SmallServer(server.BoundedHTTPServer):
        max_connections = 2
        header_timeout = 0.4
        request_timeout = 0.5
        io_timeout = 2.0  # Absolute deadlines must win even with active traffic.

    httpd = SmallServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.02})
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def connect(httpd):
    return socket.create_connection(httpd.server_address, timeout=2)


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def active(httpd):
    with httpd._active_lock:
        return len(httpd._active)


@pytest.mark.parametrize('name', [b'Authorization', b'X-Agent-Token'])
@pytest.mark.parametrize('value', [b'Bearer \xff', b'\xc3\xa9', b'\x80invalid'])
def test_non_ascii_auth_returns_401_and_server_recovers(name, value):
    with bridge() as httpd:
        with connect(httpd) as client:
            client.sendall(b'GET /v1/status HTTP/1.0\r\n' + name + b': ' + value + b'\r\n\r\n')
            assert b' 401 ' in client.recv(8192)
        with connect(httpd) as client:
            client.sendall(b'GET /health HTTP/1.0\r\n\r\n')
            assert b' 200 ' in client.recv(8192)


def test_connection_cap_and_idle_header_deadline_release_capacity():
    with bridge() as httpd, contextlib.ExitStack() as stack:
        clients = [stack.enter_context(connect(httpd)) for _ in range(2)]
        for client in clients:
            client.sendall(b'GET /health HTTP/1.1\r\nHost:')
        wait_for(lambda: active(httpd) == 2)
        with connect(httpd) as extra:
            raw=b''
            while chunk:=extra.recv(8192): raw+=chunk
            assert b' 503 ' in raw
            assert json.loads(raw.split(b'\r\n\r\n',1)[1])['error_detail']['code']=='temporarily_unavailable'
        wait_for(lambda: active(httpd) == 0)
        for client in clients:
            assert client.recv(8192) == b''
        with connect(httpd) as client:
            client.sendall(b'GET /health HTTP/1.0\r\n\r\n')
            assert b' 200 ' in client.recv(8192)


@pytest.mark.parametrize('body', [False, True])
def test_trickling_headers_and_body_cannot_extend_absolute_deadline(body):
    with bridge() as httpd, connect(httpd) as client:
        start = time.monotonic()
        if body:
            client.sendall(('POST /v1/library/settings HTTP/1.0\r\n'
                            f'Authorization: Bearer {server.STATE.token}\r\n'
                            'Content-Length: 60000\r\n\r\n').encode())
        else:
            client.sendall(b'GET /health HTTP/1.0\r\nX-Trickle: ')
        wait_for(lambda: active(httpd) == 1)
        while active(httpd) and time.monotonic() - start < 1.5:
            try:
                client.sendall(b' ')
            except OSError:
                break
            time.sleep(0.05)
        wait_for(lambda: active(httpd) == 0)
        assert time.monotonic() - start < 1.5


def test_stalled_reader_does_not_hold_response_worker():
    class LargeResponse(server.Handler):
        def do_GET(self):
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            self._send(200, {'data': 'x' * (12 * 1024 * 1024)})

    with bridge(LargeResponse) as httpd, connect(httpd) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        client.sendall(b'GET /health HTTP/1.0\r\n\r\n')
        wait_for(lambda: active(httpd) == 1)
        wait_for(lambda: active(httpd) == 0, timeout=1.5)


def test_server_close_interrupts_idle_clients():
    with bridge() as httpd, connect(httpd) as client:
        wait_for(lambda: active(httpd) == 1)
        httpd.shutdown()
        httpd.server_close()
        wait_for(lambda: active(httpd) == 0)
        assert client.recv(8192) == b''
