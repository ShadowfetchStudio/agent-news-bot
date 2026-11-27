from __future__ import annotations

import copy
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))
from agent_newsbot import worldmonitor, engine, server
from agent_newsbot.stories import StoryStore
from agent_newsbot.store import FreshnessStore
from agent_newsbot.config import Article, DeskResult

NOW = datetime.fromtimestamp(1795780800, timezone.utc)
FIXTURE = ROOT / "tests/fixtures/worldmonitor-digest.json"


def article(url="https://example.com/a", title="NASA launches lunar orbiter to map south pole craters", summary="Launch report", provider="direct"):
    from agent_newsbot.store import article_id
    return dict(id=article_id(url, title), desk="science", category="Science", title=title, url=url,
                source="Example", published=NOW.isoformat(), summary=summary, tweet="draft", tweet_chars=5,
                hashtags=["#Science"], extra={"provider": provider, "metadata": {}})


@contextmanager
def provider(payload=None, status=200, redirect=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("X-WorldMonitor-Key")))
            self.send_response(status)
            if redirect:
                self.send_header("Location", "/stolen-key")
            self.end_headers()
            body = payload if isinstance(payload, bytes) else json.dumps(payload or {}).encode()
            self.wfile.write(body)
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{httpd.server_port}", calls
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


def test_digest_contract_preserves_context_and_rejects_invalid_items():
    results = worldmonitor.parse_digest(json.loads(FIXTURE.read_text()), 12, 20, NOW)
    items = [a for r in results for a in r.articles]
    assert len(items) == 2
    assert items[0].url == "https://science.example/lunar"
    assert items[0].summary == "A new mapping mission."
    assert items[0].extra["metadata"]["locationName"] == "Florida"
    assert items[0].extra["metadata"]["credibilityUnassessed"] is True
    assert sum(r.skipped_old for r in results) == 1
    assert any("partial" in e for r in results for e in r.source_errors)
    assert any("2 invalid" in e for r in results for e in r.source_errors)


@pytest.mark.parametrize("url", ["http://remote.example", "https://user:pass@example.com", "https://example.com?key=x", "file:///tmp/a", "https://example.com/api", "https://example.com/#x"])
def test_unsafe_configuration_rejected(url):
    with pytest.raises(ValueError): worldmonitor.validate_base_url(url)


def test_disabled_makes_no_requests(monkeypatch):
    monkeypatch.delenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network called"))
    assert worldmonitor.fetch(12, 20) == []


@pytest.mark.parametrize("status,body", [(401, {}), (429, {}), (503, {}), (200, b"not-json"), (200, {"categories": []})])
def test_provider_failure_isolated_and_credentials_redacted(monkeypatch, status, body):
    with provider(body, status) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", "fixture-secret")
        result = worldmonitor.fetch(12, 20)
        assert result[0].source_errors and not result[0].articles
        assert "fixture-secret" not in str(result)
        assert calls[0][1] == "fixture-secret"


def test_redirect_does_not_forward_credentials(monkeypatch):
    with provider({}, 302, True) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", "fixture-secret")
        assert worldmonitor.fetch(12, 20)[0].source_errors == ["World Monitor HTTP 302"]
        assert len(calls) == 1


def test_sources_revisions_restart_and_independent_delivery(tmp_path):
    path = tmp_path / "stories.sqlite"
    journal = StoryStore(path)
    first = article(); second = article("https://second.example/a")
    new = journal.ingest([first, second])
    # A transaction delivers the complete grouped story once, not interim source additions.
    assert len(new) == 1 and len(new[0]["sources"]) == 2 and new[0]["revision"] == 1
    assert journal.ingest([first, second]) == []
    journal = StoryStore(path)
    first["title"] += ": mission delayed"
    update = journal.ingest([first])[0]
    assert update["revision"] == 2 and update["change"] == "updated"
    assert len(update["sources"]) == 2
    one = journal.pending("briefing", 1)
    assert one == journal.pending("briefing", 1)
    assert one["next_cursor"] == 1
    assert journal.acknowledge("briefing", 1)["cursor"] == 1
    assert journal.pending("briefing")["count"] == 1
    assert journal.pending("video")["count"] == 2
    assert StoryStore(path).pending("briefing")["cursor"] == 1
    with pytest.raises(ValueError): journal.acknowledge("video", 3)
    assert journal.acknowledge("briefing", 0)["cursor"] == 1


def test_provider_metadata_changes_do_not_create_editorial_updates(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    a = article(provider="worldmonitor")
    journal.ingest([a])
    a["extra"]["metadata"] = {"importanceScore": 76, "storyMeta": {"mentionCount": 2}}
    assert journal.ingest([a]) == []
    assert journal.pending("agent")["count"] == 1


def test_direct_and_digest_snippets_do_not_oscillate(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    direct = article(summary="Full RSS summary with more detail")
    digest = article(summary="Short digest", provider="worldmonitor")
    journal.ingest([direct, digest])
    assert journal.ingest([direct, digest]) == []
    update = copy.deepcopy(direct); update["summary"] += " Updated."
    changed = journal.ingest([update, digest])[0]
    assert changed["summary"] == update["summary"]
    assert changed["sources"][0]["providers"] == ["direct", "worldmonitor"]


def test_invalid_batch_does_not_create_partial_delivery(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    with pytest.raises(KeyError): journal.ingest([article(), {}])
    assert journal.pending("agent")["count"] == 0
    assert journal.ingest([article()])[0]["revision"] == 1


def test_engine_combines_provider_with_existing_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ALL_DESKS", ["science"])
    a = article(); a.pop("category")
    monkeypatch.setattr(engine, "fetch_desk", lambda *args: DeskResult("science", [Article(**a)]))
    b = copy.deepcopy(a); b["url"] = "https://second.example/a"; b["id"] = "second"
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [DeskResult("science", [Article(**b)], source_errors=["World Monitor coverage: partial"])])
    result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
    assert result["total_articles"] == 1
    assert len(result["articles"][0]["sources"]) == 2
    assert len(result["desks"]) == 1
    assert result["desks"][0]["errors"]
    assert engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))["total_articles"] == 0


@pytest.fixture(scope="session")
def swift_harness(tmp_path_factory):
    if sys.platform != "darwin": pytest.skip("Swift macOS harness")
    dest = tmp_path_factory.mktemp("swift") / "integration-harness"
    files = ["Models", "Coverage", "SnapshotPolicy", "Freshness", "Hashtagger", "XText", "StoryStore", "WorldMonitorClient", "AgentBridge", "MarketsClient", "SkillInstaller", "SettingsStore", "Paths", "RSSClient", "Feeds"]
    subprocess.run(["swiftc", *[str(ROOT / f"Sources/{f}.swift") for f in files], str(ROOT / "tests/IntegrationHarness.swift"), "-o", str(dest)], check=True, capture_output=True)
    shutil.copyfile(ROOT / "linux/agent_newsbot/vendor/twitter-text.js", dest.parent / "twitter-text.js")
    return dest


def swift(binary, *args):
    return json.loads(subprocess.check_output([str(binary), *map(str, args)]))


def test_swift_python_digest_parity(swift_harness):
    native = swift(swift_harness, "digest", FIXTURE)
    py = worldmonitor.parse_digest(json.loads(FIXTURE.read_text()), 12, 20, NOW)
    native_items = sorted(native["articles"], key=lambda a: a["url"])
    py_items = sorted([a.as_dict() for r in py for a in r.articles], key=lambda a: a["url"])
    for native_item, py_item in zip(native_items, py_items, strict=True):
        for key in ("id", "title", "url", "source", "desk", "summary", "extra"):
            assert native_item[key] == py_item[key], key
    assert sorted(native["errors"]) == sorted(e for r in py for e in r.source_errors)


def test_swift_persistent_updates_and_consumers(swift_harness, tmp_path):
    data = tmp_path / "input.json"; db = tmp_path / "stories.sqlite"
    rows = [article(), article("https://second.example/a")]
    data.write_text(json.dumps(rows))
    result = swift(swift_harness, "ingest", db, data)
    assert len(result["articles"]) == 1
    assert len(result["articles"][0]["sources"]) == 2
    assert swift(swift_harness, "ingest", db, data)["articles"] == []
    assert swift(swift_harness, "pending", db, "a")["count"] == 1
    assert swift(swift_harness, "ack", db, "a", 1)["cursor"] == 1
    assert swift(swift_harness, "pending", db, "a")["count"] == 0
    assert swift(swift_harness, "pending", db, "b")["count"] == 1
    rows[0]["summary"] = "A corrected report."
    data.write_text(json.dumps(rows))
    assert swift(swift_harness, "ingest", db, data)["articles"][0]["revision"] == 2
    assert swift(swift_harness, "pending", db, "a")["count"] == 1


def test_swift_connector_http_and_redirect(swift_harness):
    payload = json.loads(FIXTURE.read_text())
    for row in payload["categories"]["tech"]["items"]:
        if row.get("publishedAt", 0) > 1000:
            row["publishedAt"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    with provider(payload) as (url, calls):
        result = swift(swift_harness, "fetch", url)
        assert len(result["articles"]) == 2
        assert calls[0][1] == "test-fixture-key"
        assert "variant=tech" in calls[0][0]
    with provider({}, 302, True) as (url, calls):
        assert swift(swift_harness, "fetch", url)["errors"] == ["World Monitor HTTP 302"]
        assert len(calls) == 1


def test_oversized_body_and_timeout_are_isolated(monkeypatch):
    monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
    with provider(b" " * 2048) as (url, _):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setattr(worldmonitor, "MAX_BYTES", 1024)
        assert worldmonitor.fetch(12, 20)[0].source_errors
    def timeout(*a, **kw): raise requests.Timeout("do-not-leak-private-url")
    monkeypatch.setattr(requests, "get", timeout)
    result = worldmonitor.fetch(12, 20)
    assert result[0].source_errors
    assert "do-not-leak" not in str(result)


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_real_http_consumer_delivery(runtime, request, tmp_path, monkeypatch):
    token = "integration-test-token"
    if runtime == "python":
        monkeypatch.setattr(server, "HOME", tmp_path)
        monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
        state = server.NewsState()
        state.stories.ingest([article()])
        token = state.token
        monkeypatch.setattr(server, "STATE", state)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        port = httpd.server_port
        def cleanup(): httpd.shutdown(); httpd.server_close(); thread.join()
    else:
        binary = request.getfixturevalue("swift_harness")
        data = tmp_path / "input.json"; data.write_text(json.dumps([article()]))
        db = tmp_path / "stories.sqlite"
        swift(binary, "ingest", db, data)
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        proc = subprocess.Popen([str(binary), "serve", str(db), str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import select
        assert select.select([proc.stdout], [], [], 10)[0], "Swift server startup timed out"
        assert proc.stdout.readline().strip() == b"ready"
        def cleanup(): proc.terminate(); proc.wait(timeout=10)
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert requests.get(base + "/v1/events?consumer=a", timeout=5).status_code == 401
        page = requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json()
        assert page["count"] == 1
        story_id = page["events"][0]["story"]["id"]
        detail = requests.get(base + "/v1/stories/" + story_id, headers=headers, timeout=5)
        assert detail.status_code == 200 and detail.json()["story"]["id"] == story_id
        history = requests.get(base + "/v1/stories/" + story_id + "/revisions", headers=headers, timeout=5)
        assert history.status_code == 200 and len(history.json()["revisions"]) == 1
        assert requests.get(base + "/v1/stories/" + story_id + "/revisions?limit=0", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/stories/missing", headers=headers, timeout=5).status_code == 404
        assert requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json() == page
        assert requests.post(base + "/v1/events/ack?consumer=a&through=2", headers=headers, timeout=5).status_code == 400
        ack = requests.post(base + "/v1/events/ack?consumer=a&through=1", headers=headers, timeout=5)
        assert ack.status_code == 200 and ack.json()["cursor"] == 1
        assert requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json()["count"] == 0
        assert requests.get(base + "/v1/events?consumer=b", headers=headers, timeout=5).json()["count"] == 1
        assert requests.get(base + "/v1/events?consumer=a&limit=-1", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/events?consumer=bad%0Aname", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/events/ack?consumer=a&through=1", headers=headers, timeout=5).status_code == 405
        assert requests.post(base + "/v1/events?consumer=a", headers=headers, timeout=5).status_code == 405
    finally: cleanup()


def test_python_full_provider_http_ingestion(monkeypatch, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    for row in payload["categories"]["tech"]["items"]:
        if row.get("publishedAt", 0) > 1000:
            row["publishedAt"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    with provider(payload) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setattr(engine, "ALL_DESKS", [])
        result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
        assert result["total_articles"] == 1
        assert len(result["articles"][0]["sources"]) == 2
        assert StoryStore(tmp_path / "stories.sqlite").pending("video")["count"] == 1
        assert len(calls) == 1


def test_sources_different_subjects_not_grouped(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    stories = journal.ingest([article(), article("https://x.example/a", "Central bank changes benchmark interest rate")])
    assert len(stories) == 2


def test_failed_journal_write_does_not_acknowledge(tmp_path, monkeypatch):
    journal = StoryStore(tmp_path / "stories.sqlite")
    journal.ingest([article()]); journal.pending("agent")
    import sqlite3
    monkeypatch.setattr(journal, "connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError()))
    with pytest.raises(sqlite3.Error): journal.acknowledge("agent", 1)
    assert StoryStore(tmp_path / "stories.sqlite").pending("agent")["count"] == 1


def test_failed_journal_does_not_commit_rss_cache(tmp_path, monkeypatch):
    db = FreshnessStore(tmp_path / "seen.sqlite")
    class Response:
        status_code = 200
        headers = {"ETag": '"test-etag"'}
        content = b'<rss><channel><item><title>NASA launches lunar orbiter to map south pole craters</title><link>https://example.com/a</link></item></channel></rss>'
        def raise_for_status(self): pass
    monkeypatch.setattr(engine, "ALL_DESKS", ["science"])
    monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [("https://example.com/feed", "Example")]})
    monkeypatch.setattr(engine, "_get", lambda *a, **kw: Response())
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [])
    original = StoryStore.ingest
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(StoryStore, "ingest", fail)
    with pytest.raises(OSError): engine.fetch_all(db)
    assert db.feed_cache("https://example.com/feed") == (None, None)
    monkeypatch.setattr(StoryStore, "ingest", original)
    assert engine.fetch_all(db)["total_articles"] == 1
    assert db.feed_cache("https://example.com/feed")[0] == '"test-etag"'


def test_overlapping_desks_do_not_emit_oscillating_updates(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    long = article(summary="A detailed RSS summary")
    short = article(summary="Short summary"); short["desk"] = "tech"
    assert len(journal.ingest([short, long])) == 1
    assert journal.ingest([long, short]) == []
    assert journal.pending("agent")["count"] == 1


def test_concurrent_fetches_emit_one_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "stories.sqlite"
    journals = [StoryStore(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: j.ingest([article()]), journals))
    assert sum(len(r) for r in results) == 1
    assert journals[0].pending("agent")["count"] == 1


def test_failed_journal_does_not_commit_market_quote(tmp_path, monkeypatch):
    db = FreshnessStore(tmp_path / "seen.sqlite")
    class Response:
        def json(self): return {"chart": {"result": [{"meta": {"regularMarketPrice": 200, "chartPreviousClose": 100}}]}}
    monkeypatch.setattr(engine, "_get", lambda *a, **kw: Response())
    monkeypatch.setattr(engine, "ALL_DESKS", ["markets"])
    monkeypatch.setattr(engine, "MARKET_SYMBOLS", "TEST")
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [])
    original = StoryStore.ingest
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(StoryStore, "ingest", fail)
    with pytest.raises(OSError): engine.fetch_all(db)
    assert db.last_quote("TEST") is None
    monkeypatch.setattr(StoryStore, "ingest", original)
    assert engine.fetch_all(db)["total_articles"] == 1
    assert db.last_quote("TEST") is not None


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_database_failure_rolls_back_entire_batch(runtime, request, tmp_path):
    import sqlite3
    db = tmp_path / "stories.sqlite"
    journal = StoryStore(db)
    first = article()
    second = article("https://z.example/b", "Central bank changes benchmark interest rate")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON stories WHEN NEW.id='" + second["id"] + "' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    if runtime == "python":
        with pytest.raises(sqlite3.Error): journal.ingest([first, second])
    else:
        binary = request.getfixturevalue("swift_harness")
        data = tmp_path / "input.json"; data.write_text(json.dumps([first, second]))
        assert swift(binary, "ingest-error", db, data)["failed"] is True
    assert journal.pending("agent")["count"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM members").fetchone()[0] == 0


@contextmanager
def native_bridge(binary, db, mode="serve"):
    import socket, select
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([str(binary), mode, str(db), str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([proc.stdout], [], [], 10)[0]
        assert proc.stdout.readline().strip() == b"ready"
        yield f"http://127.0.0.1:{port}", proc
    finally:
        if proc.poll() is None: proc.terminate()
        proc.wait(timeout=10)


def test_native_listener_restarts_and_failed_port_change(swift_harness, tmp_path):
    import socket
    with socket.socket() as a, socket.socket() as b:
        a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0))
        ports = a.getsockname()[1], b.getsockname()[1]
    result = swift(swift_harness, "lifecycle", tmp_path / "journal.sqlite", *ports)
    assert result == {"failures": 0, "occupied_rejected": True, "old_listener_preserved": True}


def test_native_disconnect_does_not_kill_bridge(swift_harness, tmp_path):
    import socket, time
    from urllib.parse import urlsplit
    a = article(summary="x" * 1_000_000)
    data = tmp_path / "input.json"; data.write_text(json.dumps([a]))
    db = tmp_path / "journal.sqlite"
    swift(swift_harness, "ingest", db, data)
    with native_bridge(swift_harness, db) as (base, proc):
        for _ in range(20):
            with socket.create_connection(("127.0.0.1", urlsplit(base).port)) as sock:
                sock.sendall(b"GET /v1/events?consumer=cancel HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer integration-test-token\r\n\r\n")
                sock.shutdown(socket.SHUT_RDWR)
            time.sleep(.01)
        assert proc.poll() is None
        assert requests.get(base + "/health", timeout=5).status_code == 200


@pytest.mark.parametrize("runtime", ["python", "swift"])
@pytest.mark.parametrize("fails", [False, True])
def test_fetch_job_truthful_completion(runtime, fails, request, tmp_path, monkeypatch):
    import time
    with contextlib.ExitStack() as stack:
        if runtime == "swift":
            binary = request.getfixturevalue("swift_harness")
            base, _ = stack.enter_context(native_bridge(binary, tmp_path / "stories.sqlite", "serve-fail" if fails else "serve-slow"))
            token = "integration-test-token"
        else:
            monkeypatch.setattr(server, "HOME", tmp_path)
            monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
            state = server.NewsState(); monkeypatch.setattr(server, "STATE", state)
            def fetch(*args):
                time.sleep(2)
                if fails: raise OSError("fixture storage failure")
                return {"articles": [], "desks": []}
            monkeypatch.setattr(server, "fetch_all", fetch)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            def stop(): httpd.shutdown(); httpd.server_close(); thread.join()
            stack.callback(stop)
            base = f"http://127.0.0.1:{httpd.server_port}"; token = state.token
        headers = {"Authorization": "Bearer " + token}
        start = time.monotonic()
        response = requests.post(base + "/v1/fetch", headers=headers, timeout=1.5)
        assert response.status_code == 202
        assert time.monotonic() - start < 1.5
        accepted = response.json()
        assert accepted["fetch_job"]["state"] == "running"
        assert "total_articles" not in accepted
        assert requests.post(base + "/v1/fetch", headers=headers, timeout=1).status_code == 409
        for _ in range(80):
            status = requests.get(base + "/v1/status", headers=headers, timeout=1).json()
            if status["fetch_job"]["state"] != "running": break
            time.sleep(.05)
        assert status["fetch_job"]["id"] == accepted["fetch_job"]["id"]
        assert status["fetch_job"]["state"] == ("failed" if fails else "completed")
        assert status["fetching"] is False
        assert ("error" in status["fetch_job"]) == fails


@pytest.mark.parametrize("symbols", ["AAA,BBB", "BBB,AAA"])
@pytest.mark.parametrize("limit", [0, 1, 2])
def test_market_checkpoint_matches_selected_symbol(monkeypatch, tmp_path, symbols, limit):
    class Quote:
        def __init__(self, url): self.url = url
        def json(self):
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 101 if "/AAA?" in self.url else 150, "chartPreviousClose": 100}}]}}
    monkeypatch.setattr(engine, "_get", lambda url, **kw: Quote(url))
    result = engine.fetch_markets(FreshnessStore(tmp_path / "seen.sqlite"), symbols, limit)
    assert {q[0] for q in result.quote_updates} == {a.extra["symbol"] for a in result.articles}
    if limit == 1: assert result.articles[0].extra["symbol"] == "BBB"


def test_swift_market_checkpoint_and_skill_preservation(swift_harness, tmp_path):
    a = article(); a["desk"] = "markets"; a["extra"] = {"symbol": "BBB"}
    data = tmp_path / "articles.json"; data.write_text(json.dumps([a]))
    assert swift(swift_harness, "market-checkpoints", data)["symbols"] == ["BBB"]
    skill = tmp_path / "skills/agent-news-bot/SKILL.md"
    assert swift(swift_harness, "skill-install", skill, "custom first content")["installed"]
    assert not swift(swift_harness, "skill-install", skill, "replacement")["installed"]
    assert skill.read_text() == "custom first content"


def test_undated_stories_expire_and_carry_counts_are_explicit(tmp_path, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    old = article(); old.update(published=None, first_seen="2000-01-01T00:00:00Z")
    recent = article("https://example.com/recent"); recent.update(published=None, first_seen=datetime.now(timezone.utc).isoformat())
    previous = {"articles": [old, recent], "fetched_at": datetime.now(timezone.utc).isoformat()}
    result = state._keep_live_desks(previous, {"articles": [], "desks": [{"desk": "science", "fresh": 0}]})
    assert [a["id"] for a in result["articles"]] == [recent["id"]]
    desk = result["desks"][0]
    assert (desk["new"], desk["updated"], desk["carried"], desk["displayed"], desk["fresh"]) == (0, 0, 1, 1, 0)
    assert result["articles"][0]["first_seen"] == recent["first_seen"]
    recent["first_seen"] = (datetime.now(timezone.utc) - timedelta(hours=server.LOOKBACK_HOURS + 1)).isoformat()
    assert state._keep_live_desks(previous, {"articles": [], "desks": [{"desk": "science", "fresh": 0}]})["total_articles"] == 0


def test_restored_snapshot_is_pruned_without_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    old = article(); old.update(published=None, first_seen="2000-01-01T00:00:00Z")
    state.latest_path.write_text(json.dumps({"articles": [old], "desks": [{"desk": "science", "fresh": 1}], "fetched_at": "2000-01-01T00:00:00Z"}))
    restored = server.NewsState()
    assert restored.snapshot["total_articles"] == 0
    assert restored.snapshot["desks"][0]["displayed"] == 0


def test_keychain_errors_do_not_erase_or_hide_credentials(swift_harness):
    assert swift(swift_harness, "keychain-failure") == {
        "write_failed": True, "read_failed": True, "deletes": 0, "adds": 0,
    }

@pytest.fixture(params=["python", "swift"])
def journal_runtime(request, tmp_path):
    db = tmp_path / "journal.sqlite"
    if request.param == "python":
        return StoryStore(db)
    binary = request.getfixturevalue("swift_harness")
    class Native:
        path = db
        def ingest(self, rows):
            data = tmp_path / "rows.json"; data.write_text(json.dumps(rows))
            return swift(binary, "ingest", db, data)["articles"]
        def pending(self, consumer): return swift(binary, "pending", db, consumer)
        def story(self, story_id): return swift(binary, "story", db, story_id)["story"]
        def revisions(self, story_id): return swift(binary, "revisions", db, story_id)["revisions"]
    return Native()


def test_enrichment_delivered_and_score_churn_stays_quiet(journal_runtime):
    direct = article(summary="Full direct account with detail")
    digest = article(summary="Short digest", provider="worldmonitor")
    digest["extra"]["metadata"] = {"locationName": "Florida", "importanceScore": 80}
    rows = journal_runtime.ingest([direct, digest])
    event = journal_runtime.pending("test")["events"]
    assert len(event) == 1 and len(rows) == 1
    assert rows[0]["sources"][0]["metadata"]["locationName"] == "Florida"
    assert event[0]["story"]["sources"] == rows[0]["sources"]
    digest["extra"]["metadata"]["importanceScore"] = 85
    assert journal_runtime.ingest([direct, digest]) == []
    assert journal_runtime.story(rows[0]["id"])["sources"][0]["metadata"]["importanceScore"] == 85
    digest["extra"]["metadata"]["locationName"] = "California"
    enriched = journal_runtime.ingest([direct, digest])
    assert enriched[0]["change"] == "enriched"
    assert journal_runtime.pending("test")["count"] == 2


def test_newer_provider_correction_does_not_revert_to_unchanged_rss(journal_runtime):
    direct = article(summary="Full original reporting")
    digest = article(provider="worldmonitor")
    first = journal_runtime.ingest([direct, digest])[0]
    digest["title"] = "NASA cancels lunar orbiter launch after engine failure"
    digest["summary"] = "The launch has been cancelled."
    corrected = journal_runtime.ingest([digest])[0]
    assert corrected["title"] == digest["title"]
    assert corrected["disagreement"] is True
    assert journal_runtime.ingest([direct, digest]) == []
    current = journal_runtime.story(first["id"])
    assert current["title"] == digest["title"]
    assert current["sources"][0]["observations"]["direct"]["title"] == direct["title"]
    history = journal_runtime.revisions(first["id"])
    assert len(history) == 2
    assert history[-1]["diff"]["title"] == {"before": direct["title"], "after": digest["title"]}


def test_desk_membership_and_disagreement_survive_grouping(journal_runtime):
    first = article(title="NASA confirms lunar orbiter launch will proceed despite engine issue")
    same = copy.deepcopy(first); same["desk"] = "tech"
    other = article("https://second.example/report", title="NASA confirms lunar orbiter launch will not proceed despite engine issue")
    story = journal_runtime.ingest([first, same, other])[0]
    assert story["desks"] == ["science", "tech"]
    assert story["disagreement"] is True
    assert len(story["sources"]) == 2
    assert journal_runtime.ingest([first, same, other]) == []


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_rss_cached_document_recovers_cap_and_lookback(runtime, request, tmp_path, monkeypatch):
    from email.utils import format_datetime
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    recent, old = format_datetime(now), format_datetime(now - timedelta(hours=4))
    body = f'<rss version="2.0"><channel><title>Fixture</title><item><title>Recent</title><link>https://example.com/recent</link><pubDate>{recent}</pubDate></item><item><title>Older</title><link>https://example.com/older</link><pubDate>{old}</pubDate></item></channel></rss>'.encode()
    calls = []
    class Feed(BaseHTTPRequestHandler):
        def do_GET(self):
            cached = self.headers.get("If-None-Match") == '"stable"'
            calls.append(cached)
            self.send_response(304 if cached else 200)
            self.send_header("ETag", '"stable"'); self.end_headers()
            if not cached: self.wfile.write(body)
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Feed)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
    url = f"http://127.0.0.1:{httpd.server_port}/rss"
    cache = FreshnessStore(tmp_path / "seen.sqlite")
    # Upgrade case: the old version stored validators without a recoverable body.
    cache.save_feed_cache(url, '"stable"', None)
    import sqlite3
    with sqlite3.connect(tmp_path / "swift-seen.sqlite") as db:
        db.execute("CREATE TABLE feed_cache(url TEXT PRIMARY KEY,etag TEXT,last_modified TEXT,updated_at REAL)")
        db.execute("INSERT INTO feed_cache VALUES (?,?,?,?)", (url, '"stable"', "", 0))
    def collect(lookback, limit):
        if runtime == "swift":
            binary = request.getfixturevalue("swift_harness")
            result = swift(binary, "rss", url, tmp_path / "swift-seen.sqlite", lookback, limit)
            assert not result["errors"]
            return result["articles"]
        monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [(url, "Fixture")]})
        result = engine.fetch_rss("science", cache, lookback, limit)
        assert not result.source_errors
        for update in result.feed_cache_updates: cache.save_feed_cache(*update)
        return [a.as_dict() for a in result.articles]
    try:
        assert len(collect(2, 1)) == 1
        assert len(collect(12, 1)) == 1
        assert len(collect(12, 2)) == 2
        assert calls == [False, True, True]
    finally: httpd.shutdown(); httpd.server_close(); worker.join()

@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_canonical_identity_contract(runtime, request):
    from agent_newsbot.store import canonical_url, article_id
    fixture = ROOT / "tests/fixtures/canonical-urls.json"
    rows = json.loads(fixture.read_text())
    if runtime == "swift":
        actual = swift(request.getfixturevalue("swift_harness"), "canonical-fixtures", fixture)["items"]
    else:
        actual = [{"canonical": canonical_url(r["raw"]), "id": article_id(r["raw"], "")} for r in rows]
    for row, item in zip(rows, actual, strict=True):
        assert item["canonical"] == row["canonical"], row["raw"]
        assert item["id"] == article_id(row["canonical"], ""), row["raw"]
    assert actual[1]["id"] != actual[2]["id"]
    assert actual[6]["id"] != actual[7]["id"]


def test_identity_upgrade_keeps_history_and_old_story_id(journal_runtime):
    import sqlite3
    from agent_newsbot.store import article_id
    old_url = "https://example.com:443/migration"
    legacy = article(old_url); legacy["id"] = "legacy-stable-id"
    source = {k: legacy[k] for k in ("url", "title", "summary", "source", "published")}
    source.update(providers=["direct"], metadata={})
    legacy.update(sources=[source], revision=1, first_seen="2026-11-27T00:00:00Z")
    # Seed an actual pre-v2 database record, including an immutable delivered event.
    with sqlite3.connect(journal_runtime.path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS members(url TEXT PRIMARY KEY,story_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,story_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);''')
        db.execute("DELETE FROM migrations WHERE name='canonical-v2'")
        db.execute("INSERT INTO stories VALUES (?,?,?)", (legacy["id"], json.dumps(legacy), datetime.now(timezone.utc).timestamp()))
        db.execute("INSERT INTO members VALUES (?,?)", (old_url, legacy["id"]))
        db.execute("INSERT INTO events(story_id,payload) VALUES (?,?)", (legacy["id"], json.dumps(legacy)))
    if isinstance(journal_runtime, StoryStore): journal_runtime = StoryStore(journal_runtime.path)
    updated = article("https://example.com/migration", title=legacy["title"] + ": corrected")
    new = journal_runtime.ingest([updated])[0]
    assert new["id"] == legacy["id"]
    assert journal_runtime.story(article_id(updated["url"], ""))["id"] == legacy["id"]
    history = journal_runtime.revisions(legacy["id"])
    assert len(history) == 2
    assert history[0]["story"]["url"] == old_url
    assert history[1]["story"]["title"] == updated["title"]


def test_late_provider_enrichment_is_a_distinct_event_type(journal_runtime):
    direct = article(summary="A detailed direct report")
    journal_runtime.ingest([direct])
    digest = article(summary="Short", provider="worldmonitor")
    digest["extra"]["metadata"] = {"locationName": "Florida"}
    result = journal_runtime.ingest([digest])
    assert result[0]["change"] == "enriched"
    assert result[0]["summary"] == direct["summary"]
    assert journal_runtime.ingest([direct, digest]) == []



def test_native_atom_markup_alternate_link_and_invalid_document(swift_harness, tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Correct title</title><link rel="self" href="https://example.com/feed"/><link rel="alternate" type="text/html" href="https://example.com/story"/><summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Hello <b>world</b></p><p>Second paragraph</p></div></summary><source><title>Not the article title</title></source></entry></feed>')
    row = swift(swift_harness, "rss-parse", feed)["items"][0]
    assert row == {"title": "Correct title", "url": "https://example.com/story", "summary": "Hello world Second paragraph"}
    feed.write_text('<html><body>Maintenance page</body></html>')
    assert swift(swift_harness, "rss-parse", feed)["error"] == "invalid feed"
    feed.write_text('<rss><channel><item><title>Incomplete')
    assert swift(swift_harness, "rss-parse", feed)["error"] == "invalid feed"


def test_official_x_weighted_conformance():
    from agent_newsbot.xtext import measure
    fixture = json.loads((ROOT / "tests/fixtures/twitter-weighted.json").read_text())
    for case in fixture["cases"]:
        assert measure(case["text"]) == case["expected"], case["description"]


def test_native_x_weighted_conformance(swift_harness, tmp_path):
    fixture = json.loads((ROOT / "tests/fixtures/twitter-weighted.json").read_text())
    path = tmp_path / "xtext.json"
    path.write_text(json.dumps(fixture["cases"]))
    actual = swift(swift_harness, "xtext", path)["items"]
    assert actual == [row["expected"] for row in fixture["cases"]]


def test_x_composer_parity_and_no_split_entities(swift_harness, tmp_path):
    from agent_newsbot.xtext import request, measure
    cases = [
        {"title": "中" * 150, "url": "https://example.com/story", "tags": []},
        {"title": "👨‍👩‍👧‍👦" * 150, "url": "https://example.com/story", "tags": ["#News"]},
        {"title": "Cafe\u0301 " * 70, "url": "https://例え.jp/path", "tags": ["#世界"]},
        {"title": "Read https://example.com/" + "a" * 300, "url": "https://example.com/2", "tags": []},
        {"title": "", "url": "https://example.com", "tags": []},
        {"title": "Story", "url": "", "tags": ["#" + "a" * 500, "#News"]},
        {"title": "Hello", "url": "", "tags": []},
    ]
    path = tmp_path / "xtext.json"
    path.write_text(json.dumps(cases))
    expected = [request(case) for case in cases]
    assert swift(swift_harness, "xtext", path)["items"] == expected
    for result in expected:
        assert result["valid"] and result["weightedLength"] <= 280
        assert measure(result["text"])["weightedLength"] == result["weightedLength"]
    assert expected[0]["weightedLength"] == 280
    family = expected[1]["text"].split("…")[0]
    assert not family.replace("👨‍👩‍👧‍👦", "")
    assert "Cafe\u0301" not in expected[2]["text"]
    assert cases[3]["title"] in expected[3]["text"]


def test_x_composer_linux_limits_and_parallel_workers():
    from concurrent.futures import ThreadPoolExecutor
    from agent_newsbot.hashtags import tweet
    from agent_newsbot.xtext import measure
    cases = [("中" * 150, "https://example.com", []), ("👩🏽‍🚀" * 180, "https://example.com", ["#Space"]),
             ("e\u0301" * 400, "https://example.com", ["#" + "a" * 500, "#News"])] * 6
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda args: tweet(*args), cases))
    for text, count in results:
        assert count <= 280 and measure(text)["valid"]
        assert count == measure(text)["weightedLength"]


@pytest.mark.parametrize("states,expected", [([], "unknown"), (["success"], "success"),
    (["unavailable", "unavailable"], "unavailable"), (["success", "unavailable"], "partial"),
    (["stale", "unavailable"], "stale"), (["partial"], "partial")])
def test_coverage_state_parity(swift_harness, tmp_path, states, expected):
    from agent_newsbot.coverage import summary
    rows = [{"source": str(i), "provider": "direct", "state": state, "detail": "", "checked_at": "fixture"} for i, state in enumerate(states)]
    path = tmp_path / "checks.json"; path.write_text(json.dumps(rows))
    native = swift(swift_harness, "coverage", path)
    assert native == summary(rows)
    assert native["state"] == expected


def test_all_failed_fetch_cannot_claim_success(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ALL_DESKS", ["science", "world"])
    monkeypatch.setattr(engine, "fetch_desk", lambda desk, *_: DeskResult(desk, [], source_errors=["unavailable"]))
    monkeypatch.setattr(worldmonitor, "fetch", lambda *_: [])
    result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
    assert result["coverage"]["state"] == "unavailable"
    assert result["total_articles"] == 0
    assert all(d["coverage"]["state"] == "unavailable" for d in result["desks"])


def test_valid_empty_feed_is_success_but_malformed_is_unavailable(tmp_path, monkeypatch):
    from agent_newsbot.coverage import summary, result_checks
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
    response = Response()
    monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [("https://example.com/feed", "Fixture")]})
    monkeypatch.setattr(engine, "_get", lambda *_args, **_kwargs: response)
    for body, expected in [(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>', "success"),
                           (b'<html><title>Maintenance</title></html>', "unavailable")]:
        response.content = body
        result = engine.fetch_rss("science", FreshnessStore(tmp_path / "seen.sqlite"), 12, 20)
        assert not result.articles
        assert summary(result_checks(result))["state"] == expected


def test_missing_weather_observation_never_becomes_zero_forecast(tmp_path, monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    replies = iter([Response({"results": [{"latitude": 40.7, "longitude": -74, "name": "New York"}]}), Response({"error": "maintenance"})])
    monkeypatch.setattr(engine, "_get", lambda *_args, **_kwargs: next(replies))
    result = engine.fetch_weather(FreshnessStore(tmp_path / "seen.sqlite"), "New York")
    assert not result.articles and result.source_errors


def test_native_separate_change_and_display_counts(swift_harness, tmp_path):
    rows = [dict(article("https://example.com/" + str(i)), change=change, carried=carried) for i, (change, carried) in
            enumerate([("new", False), ("updated", False), ("enriched", False), ("new", True)])]
    path = tmp_path / "articles.json"; path.write_text(json.dumps(rows))
    result = swift(swift_harness, "desk-counts", path)
    assert {k: result[k] for k in ("new", "updated", "carried", "fresh", "displayed")} == dict(new=1, updated=2, carried=1, fresh=3, displayed=4)


@pytest.mark.parametrize("body,expected", [(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>', "success"),
                                           (b'<html><title>Maintenance</title></html>', "unavailable")])
def test_native_rss_coverage_real_http(swift_harness, tmp_path, body, expected):
    with provider(body) as (url, _):
        result = swift(swift_harness, "rss", url, tmp_path / "cache.sqlite", 12, 20)
        assert result["coverage"]["state"] == expected
        assert not result["articles"]


def test_native_snapshot_restores_without_renewing_dates_or_coverage(swift_harness, tmp_path):
    recent = article(); recent.update(first_seen="2026-11-27T10:00:00Z")
    ancient = dict(article("https://example.com/old"), published="", first_seen="2020-01-01T00:00:00Z")
    undated = dict(article("https://example.com/undated"), published="")
    future = dict(article("https://example.com/future"), published="2030-01-01T00:00:00Z")
    payload = {"fetched_at": "2026-11-27T11:00:00Z", "articles": [recent, ancient, undated, future],
               "desks": [{"desk": "science", "coverage": {"sources": [{"source": "Fixture", "provider": "direct", "state": "success", "checked_at": "2026-11-27T11:00:00Z"}]}}]}
    path = tmp_path / "snapshot.json"; path.write_text(json.dumps(payload))
    result = swift(swift_harness, "snapshot", path)
    assert {a["id"] for a in result["articles"]} == {recent["id"], undated["id"]}
    assert all(a["carried"] for a in result["articles"])
    assert result["desks"][0]["fresh"] == 0
    assert result["desks"][0]["coverage"]["state"] == "stale"
    restored_undated = next(a for a in result["articles"] if a["id"] == undated["id"])
    assert restored_undated["first_seen"] == "2026-11-27T11:00:00Z"
    assert result["fetched_at"] == payload["fetched_at"]


def test_native_new_story_does_not_erase_other_current_stories(swift_harness, tmp_path):
    old = article(); replacement = dict(old, summary="Corrected", change="updated")
    old2 = article("https://example.com/retained")
    new = article("https://example.com/new")
    path = tmp_path / "batch.json"; path.write_text(json.dumps({"previous": [old, old2], "fresh": [replacement, new]}))
    rows = swift(swift_harness, "snapshot-merge", path)["articles"]
    assert len(rows) == 3
    assert next(a for a in rows if a["id"] == old["id"])["summary"] == "Corrected"
    assert next(a for a in rows if a["id"] == old2["id"])["carried"] is True
    assert sum(not a["carried"] for a in rows) == 2


def test_native_isolated_app_paths_and_credentials(swift_harness, tmp_path):
    env = dict(os.environ, AGENT_NEWSBOT_HOME=str(tmp_path / "isolated"))
    first = json.loads(subprocess.check_output([str(swift_harness), "paths"], env=env))
    assert first["support"] == str(tmp_path / "isolated")
    assert first["bundle"].startswith("ai.shadowfetch.agentnewsbot.test.")
    env["AGENT_NEWSBOT_HOME"] = str(tmp_path / "other")
    second = json.loads(subprocess.check_output([str(swift_harness), "paths"], env=env))
    assert second["bundle"] != first["bundle"]


def test_linux_new_story_does_not_erase_other_current_stories(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path); monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    stamp = datetime.now(timezone.utc).isoformat()
    old = dict(article(), published=stamp)
    new = dict(article("https://example.com/new"), published=stamp, change="new")
    result = state._keep_live_desks({"articles": [old], "fetched_at": stamp},
                                  {"articles": [new], "fetched_at": stamp, "desks": [{"desk": "science", "fresh": 1}]})
    assert result["total_articles"] == 2
    assert (result["desks"][0]["new"], result["desks"][0]["carried"]) == (1, 1)
