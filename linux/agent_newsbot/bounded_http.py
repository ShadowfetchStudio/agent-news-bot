"""Loopback HTTP transport with bounded workers and absolute socket deadlines."""
from __future__ import annotations

import json
import socket
from .api_contract import error_payload
import threading
from http.server import ThreadingHTTPServer


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    max_connections = 16
    header_timeout = 5.0
    request_timeout = 15.0
    io_timeout = 5.0

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.max_connections)
        self._active_lock = threading.Lock()
        self._active = set()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(0.1)
                body = json.dumps(error_payload(503, {"error": "connection capacity reached"})).encode()
                header = ("HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nRetry-After: 1\r\n"
                          "Content-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n\r\n").encode()
                request.sendall(header + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        with self._active_lock:
            self._active.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release(request)
            raise

    def _release(self, request):
        with self._active_lock:
            self._active.discard(request)
        self._slots.release()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(request)

    def server_close(self):
        with self._active_lock:
            for request in self._active:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        super().server_close()


class DeadlineHandlerMixin:
    """One request per connection; trickling bytes cannot renew the deadline.

    Socket shutdown interrupts reads/writes. It does not cancel a database operation
    already in progress; the server's connection semaphore still bounds its worker.
    """
    def setup(self):
        self.request.settimeout(getattr(self.server, 'io_timeout', 5.0))
        super().setup()
        self._deadline_lock = threading.Lock()
        self._deadline_generation = 0
        self._deadline_timer = None
        self._arm_deadline(getattr(self.server, 'header_timeout', 5.0))

    def _arm_deadline(self, seconds):
        with self._deadline_lock:
            self._deadline_generation += 1
            generation = self._deadline_generation
            if self._deadline_timer is not None:
                self._deadline_timer.cancel()

            def expire():
                with self._deadline_lock:
                    if generation != self._deadline_generation:
                        return
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            self._deadline_timer = threading.Timer(seconds, expire)
            self._deadline_timer.daemon = True
            self._deadline_timer.start()

    def parse_request(self):
        valid = super().parse_request()
        self.close_connection = True
        if valid:
            self._arm_deadline(getattr(self.server, 'request_timeout', 15.0))
        return valid

    def handle(self):
        try:
            super().handle()
        except OSError:
            pass  # Timed-out/disconnected clients are normal transport failures.

    def finish(self):
        with self._deadline_lock:
            self._deadline_generation += 1
            self._deadline_timer.cancel()
        try:
            super().finish()
        except OSError:
            pass
