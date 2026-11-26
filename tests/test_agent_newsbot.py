"""Automated test suite for Agent News Bot (Linux Python + macOS Swift parity)."""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "linux"))

from agent_newsbot import config, hashtags, server, store  # noqa: E402


def test_canonical_url_strips_tracking_and_normalizes() -> None:
    raw = "HTTPS://Example.COM/news/story/?utm_source=x&id=42&fbclid=abc&gclid=def#frag"
    assert store.canonical_url(raw) == "https://example.com/news/story?id=42"
    assert store.article_id(raw, "fallback") == store.article_id("https://example.com/news/story?id=42", "other")


def test_freshness_store_seen_quotes_cache_and_prune(tmp_path: Path) -> None:
    db = store.FreshnessStore(tmp_path / "seen.sqlite")
    assert not db.has_seen("abc123")
    db.mark("abc123", "https://example.com/a", "Test Title", "ai")
    assert db.has_seen("abc123")

    assert db.last_quote("SPY") is None
    db.save_quote("SPY", "500.00|0.01")
    assert db.last_quote("SPY") == "500.00|0.01"

    assert db.feed_cache("https://example.com/rss") == (None, None)
    db.save_feed_cache("https://example.com/rss", '"etag-1"', "Mon, 01 Jan 2026 00:00:00 GMT")
    assert db.feed_cache("https://example.com/rss") == ('"etag-1"', "Mon, 01 Jan 2026 00:00:00 GMT")

    # Prune nothing when recent, prune everything when older_than_days=0
    assert db.prune_seen(older_than_days=30) == 0
    assert db.prune_seen(older_than_days=-1) == 1
    assert not db.has_seen("abc123")


def test_fuzzy_title_dedup() -> None:
    t1 = store.title_tokens("Federal Reserve holds interest rates steady amid inflation report")
    t2 = store.title_tokens("Federal Reserve holds interest rates steady after inflation report")
    t3 = store.title_tokens("NASA launches new lunar orbiter to map south pole craters")
    assert store.is_near_duplicate_title(t1, t2)
    assert not store.is_near_duplicate_title(t1, t3)


def test_tweet_length_respects_280_limit() -> None:
    long_title = "OpenAI and NVIDIA announce a " + ("very " * 80) + "long breaking headline"
    tags = hashtags.tags("ai", long_title)
    tweet, chars = hashtags.tweet(long_title, "https://example.com/very/long/path/article", tags)
    assert chars <= 280
    assert "https://example.com/very/long/path/article" in tweet


def test_http_bridge_auth_filtering_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    state.snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_articles": 2,
        "articles": [
            {"id": "id1", "desk": "ai", "category": "AI", "title": "AI Story", "url": "https://a.test/1"},
            {"id": "id2", "desk": "tech", "category": "Tech", "title": "Tech Story", "url": "https://a.test/2"},
        ],
        "desks": [{"desk": "ai", "title": "AI", "fresh": 1, "skipped_old": 0, "skipped_dup": 0, "errors": []}],
        "categories": {},
    }
    monkeypatch.setattr(server, "STATE", state)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # 1. /health requires no auth
        with urllib.request.urlopen(f"{base}/health") as r:
            assert r.status == 200
            assert json.loads(r.read())["ok"] is True

        # 2. /v1/fresh without token -> 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/v1/fresh")
        assert exc.value.code == 401

        # 3. /v1/fresh?desk=ai&limit=1 with valid token
        req = urllib.request.Request(
            f"{base}/v1/fresh?desk=ai&limit=1",
            headers={"Authorization": f"Bearer {state.token}"},
        )
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
            assert body["count"] == 1
            assert body["articles"][0]["id"] == "id1"

        # 4. /v1/fresh?limit=abc -> 400 (not 500!)
        bad_req = urllib.request.Request(
            f"{base}/v1/fresh?limit=abc",
            headers={"Authorization": f"Bearer {state.token}"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad_req)
        assert exc.value.code == 400

        # 5. /v1/articles/id2 -> 200
        art_req = urllib.request.Request(
            f"{base}/v1/articles/id2",
            headers={"X-Agent-Token": state.token},
        )
        with urllib.request.urlopen(art_req) as r:
            assert json.loads(r.read())["article"]["title"] == "Tech Story"

        # 6. /v1/status includes fetched_at and fetching
        st_req = urllib.request.Request(
            f"{base}/v1/status",
            headers={"Authorization": f"Bearer {state.token}"},
        )
        with urllib.request.urlopen(st_req) as r:
            st = json.loads(r.read())
            assert st["ok"] is True and st["fresh"] == 2 and "fetched_at" in st and "fetching" in st
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_swift_and_python_rss_feeds_stay_in_sync() -> None:
    """Ensure Sources/Feeds.swift and linux/agent_newsbot/config.py define identical feed URLs."""
    swift_src = (REPO_ROOT / "Sources" / "Feeds.swift").read_text(encoding="utf-8")
    swift_urls = set(re.findall(r'url:\s*"([^"]+)"', swift_src))
    py_urls = {url for feeds in config.RSS_FEEDS.values() for url, _ in feeds}
    assert swift_urls == py_urls, f"Feed drift detected! Swift-only={swift_urls - py_urls}, Py-only={py_urls - swift_urls}"


def test_no_stale_personal_repo_url() -> None:
    for rel in ("Sources/Models.swift", "linux/agent_newsbot/config.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "Realbobcorbin" not in text
        assert "ShadowfetchStudio/agent-news-bot" in text
