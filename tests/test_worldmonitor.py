from __future__ import annotations

import copy
import contextlib
import json
import os
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
    first = article()
    second = article("https://second.example/a")
    new = journal.ingest([first, second])
    assert len(new) == 1 and len(new[0]["sources"]) == 2 and new[0]["revision"] == 2
    assert journal.ingest([first, second]) == []
    # Restart, then change a headline at the same URL: retain both sources.
    journal = StoryStore(path)
    first["title"] += ": mission delayed"
    update = journal.ingest([first])[0]
    assert update["revision"] == 3 and update["change"] == "updated"
    assert len(update["sources"]) == 2
    one = journal.pending("briefing", 2)
    assert one == journal.pending("briefing", 2)
    assert one["next_cursor"] == 2
    assert journal.acknowledge("briefing", 2)["cursor"] == 2
    assert journal.pending("briefing")["count"] == 1
    assert journal.pending("video")["count"] == 3
    assert StoryStore(path).pending("briefing")["cursor"] == 2
    with pytest.raises(ValueError): journal.acknowledge("video", 4)
    assert journal.acknowledge("briefing", 1)["cursor"] == 2


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
    files = ["Models", "Freshness", "Hashtagger", "StoryStore", "WorldMonitorClient", "AgentBridge", "MarketsClient", "SkillInstaller", "SettingsStore", "Paths"]
    subprocess.run(["swiftc", *[str(ROOT / f"Sources/{f}.swift") for f in files], str(ROOT / "tests/IntegrationHarness.swift"), "-o", str(dest)], check=True, capture_output=True)
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
    assert swift(swift_harness, "pending", db, "a")["count"] == 2
    assert swift(swift_harness, "ack", db, "a", 2)["cursor"] == 2
    assert swift(swift_harness, "pending", db, "a")["count"] == 0
    assert swift(swift_harness, "pending", db, "b")["count"] == 2
    rows[0]["summary"] = "A corrected report."
    data.write_text(json.dumps(rows))
    assert swift(swift_harness, "ingest", db, data)["articles"][0]["revision"] == 3
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
        assert StoryStore(tmp_path / "stories.sqlite").pending("video")["count"] == 2
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
