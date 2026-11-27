"""Bounded, isolated observations for the four-perspective follow-up audit.

Run with the project's test venv. This records current behavior, not passing
release assertions. No real credentials, publishers, user state or apps used.
"""
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
WORK = Path(tempfile.mkdtemp(prefix="anb-followup-audit-"))
os.environ["AGENT_NEWSBOT_HOME"] = str(WORK / "state")
sys.path[:0] = [str(ROOT / "linux"), str(ROOT / "tests")]
from agent_newsbot import engine, server, worldmonitor
from agent_newsbot.store import FreshnessStore
from agent_newsbot.stories import StoryStore
from test_worldmonitor import article, swift_harness, swift, native_bridge

out = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
       "work": str(WORK), "observed_at": datetime.now(timezone.utc).isoformat()}


class Factory:
    def mktemp(self, name):
        path = WORK / name
        path.mkdir()
        return path


@contextmanager
def fixture_http(body=b"", trickle=False, handler=None):
    stats = {"bytes_sent": 0, "errors": []}
    class Body(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if not trickle:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if trickle:
                    for _ in range(80):
                        self.wfile.write(b" "); self.wfile.flush(); stats["bytes_sent"] += 1
                        time.sleep(.1)
                else:
                    for start in range(0, len(body), 65536):
                        part = body[start:start+65536]
                        self.wfile.write(part); stats["bytes_sent"] += len(part)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    class HTTP(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, request, client_address):
            stats["errors"].append(type(sys.exception()).__name__)
    httpd = HTTP(("127.0.0.1", 0), handler or Body)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try: yield httpd, stats
    finally:
        httpd.shutdown(); httpd.server_close(); worker.join(timeout=2)


def raw_request(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
        conn.sendall(request)
        return conn.recv(8192).split(b"\r\n", 1)[0].decode("latin1")


binary = swift_harness.__wrapped__(Factory()) if sys.platform == "darwin" else None

# A bounded eight-client sample, not a load attack. Sockets always closed.
with fixture_http(handler=server.Handler) as (httpd, stats):
    port = httpd.server_port
    out["non_ascii_auth_python"] = raw_request(port, b"GET /v1/status HTTP/1.0\r\nAuthorization: Bearer \xe9\r\n\r\n")
    out["non_ascii_auth_python_errors"] = stats["errors"]
    sockets = []
    baseline = threading.active_count()
    try:
        for _ in range(8):
            conn = socket.create_connection(("127.0.0.1", port), timeout=2)
            conn.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n")
            sockets.append(conn)
            time.sleep(.03)
        time.sleep(6)
        out["slow_headers_python"] = {"open_clients": len(sockets), "wait_seconds": 6,
            "additional_threads": threading.active_count() - baseline}
    finally:
        for conn in sockets: conn.close()
if binary:
    with native_bridge(binary, WORK / "http.sqlite") as (base, process):
        port = int(base.rsplit(":", 1)[1])
        out["non_ascii_auth_swift"] = raw_request(port, b"GET /v1/status HTTP/1.0\r\nAuthorization: Bearer \xc3\xa9\r\n\r\n")
    class UnrelatedService(BaseHTTPRequestHandler):
        def reply(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        def do_GET(self): self.reply()
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.reply()
        def log_message(self, *args): pass
    with fixture_http(handler=UnrelatedService) as (httpd, stats):
        base = f"http://127.0.0.1:{httpd.server_port}"
        out["unrelated_service_hermes_probe"] = swift(binary, "hermes-probe", base)
        out["invalid_completion_hermes_send"] = swift(binary, "hermes-api", base)

# Twenty new science stories plus twenty retained ones, with two enabled desks.
stamp = datetime.now(timezone.utc).isoformat()
previous = [dict(article(url=f"https://example.com/old-{i}"), id=f"old-{i}", published=stamp) for i in range(20)]
fresh = [dict(article(url=f"https://example.com/new-{i}"), id=f"new-{i}", published=stamp) for i in range(20)]
server.MAX_PER_DESK = 20
merged = server.STATE._keep_live_desks({"articles": previous, "fetched_at": stamp},
    {"articles": fresh, "fetched_at": stamp, "desks": [{"desk": "science"}, {"desk": "tech"}]})
out["per_desk_cap_python"] = {"requested_max": 20, "science_displayed": len(merged["articles"])}
if binary:
    # The checked-in harness has a fixed clock for deterministic snapshots.
    fixed = datetime.fromtimestamp(1795780800, timezone.utc).isoformat()
    data = WORK / "merge.json"
    data.write_text(json.dumps({"previous": [dict(a, published=fixed) for a in previous],
                                "fresh": [dict(a, published=fixed) for a in fresh]}))
    out["per_desk_cap_swift"] = {"requested_max": 20, "science_displayed": len(swift(binary, "snapshot-merge", data)["articles"])}

# Never open these article URLs; inspect only normalization and ingestion.
rss = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture</title>
<item><title>A local file presented as a science headline</title><link>file:///tmp/audit-unused.txt</link></item>
</channel></rss>'''
with fixture_http(rss) as (httpd, stats):
    base = f"http://127.0.0.1:{httpd.server_port}"
    old = engine.RSS_FEEDS.get("science")
    try:
        engine.RSS_FEEDS["science"] = [(base, "Fixture")]
        result = engine.fetch_rss("science", FreshnessStore(WORK / "rss.sqlite"), 12, 20)
        out["unsafe_rss_python"] = {"urls": [a.url for a in result.articles], "errors": result.source_errors}
        if binary:
            result = swift(binary, "rss", base, WORK / "rss-native.sqlite", 12, 20)
            out["unsafe_rss_swift"] = {"urls": [a["url"] for a in result["articles"]], "errors": result["errors"]}
    finally: engine.RSS_FEEDS["science"] = old

large = b"x" * (6 * 1024 * 1024)
with fixture_http(large) as (httpd, stats):
    response = engine._get(f"http://127.0.0.1:{httpd.server_port}")
    out["direct_http_python"] = {"downloaded_bytes": len(response.content), "rss_limit_bytes": 4 * 1024 * 1024}
    if binary:
        stats["bytes_sent"] = 0
        result = swift(binary, "rss", f"http://127.0.0.1:{httpd.server_port}", WORK / "rss-large.sqlite", 12, 20)
        out["direct_http_swift"] = {"fixture_sent_bytes": stats["bytes_sent"], "errors": result["errors"]}

with fixture_http(trickle=True) as (httpd, stats):
    original = worldmonitor.HTTP_DEADLINE
    worldmonitor.HTTP_DEADLINE = 1.0
    start = time.monotonic()
    try:
        worldmonitor.bounded_request(f"http://127.0.0.1:{httpd.server_port}", "full", "fixture-not-a-real-key")
        result = "unexpected completion"
    except subprocess.TimeoutExpired: result = "timeout"
    finally: worldmonitor.HTTP_DEADLINE = original
    out["wm_total_deadline"] = {"result": result, "elapsed_seconds": round(time.monotonic() - start, 2)}

# Confusable reports with different actors must not silently become one event.
titles = ["NASA launches new lunar orbiter to map south pole craters",
          "China launches new lunar orbiter to map south pole craters"]
rows = [article(url=f"https://example.com/mission-{i}", title=title) for i, title in enumerate(titles)]
journal = StoryStore(WORK / "groups.sqlite")
changed = journal.ingest(rows)
out["different_actor_group_python"] = {"stories": len(changed), "source_counts": [len(a["sources"]) for a in changed],
    "disagreement": [a["disagreement"] for a in changed], "input_titles": titles}
if binary:
    data = WORK / "groups.json"; data.write_text(json.dumps(rows))
    result = swift(binary, "ingest", WORK / "groups-native.sqlite", data)
    out["different_actor_group_swift"] = {"stories": len(result["articles"]), "source_counts": [len(a["sources"]) for a in result["articles"]]}

# Execute the exact token helper body in an isolated container, not the GUI app.
if binary:
    source = (ROOT / "Sources/Engine.swift").read_text()
    method = source.split("    private func loadOrCreateToken() -> String {", 1)[1].split("\n    func installAgentSkills()", 1)[0]
    harness = WORK / "TokenProbe.swift"
    harness.write_text('import Foundation\nstruct TokenProbe {\n func loadOrCreateToken() -> String {' + method +
        '\n}\n@main struct Run { static func main() throws {\n' +
        'try FileManager.default.createDirectory(at: AppPaths.tokenFile, withIntermediateDirectories: true)\n' +
        'let probe = TokenProbe(), a = probe.loadOrCreateToken(), b = probe.loadOrCreateToken()\n' +
        'let data = try JSONSerialization.data(withJSONObject: ["same_token": a == b, "returned_length": a.count, "token_path_is_directory": true])\n' +
        'print(String(decoding: data, as: UTF8.self))\n}}\n')
    exe = WORK / "token-probe"
    subprocess.run(["swiftc", str(ROOT / "Sources/Paths.swift"), str(harness), "-o", str(exe)], check=True, capture_output=True)
    out["native_token_write_failure"] = json.loads(subprocess.check_output([str(exe)], env=dict(os.environ, AGENT_NEWSBOT_HOME=str(WORK / "token-home"))))

destination = Path(__file__).with_name("observations.json")
destination.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
