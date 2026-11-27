"""Optional World Monitor REST adapter; no platform code is embedded.

Contract: proto/worldmonitor/news/v1/{service,list_feed_digest,news_item}.proto.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from .config import Article, DeskResult, DESK_TITLES
from .hashtags import tags, tweet
from .store import article_id, canonical_url, iso, strip_html

VARIANTS = {"full", "tech", "finance", "happy", "commodity", "energy"}
MAX_BYTES = 4 * 1024 * 1024


def validate_base_url(base: str) -> str:
    p = urlsplit(base)
    local = p.hostname in ("localhost", "127.0.0.1", "::1")
    if (not p.hostname or p.username or p.password or p.query or p.fragment
            or p.path not in ("", "/") or p.scheme not in ("http", "https")
            or (p.scheme == "http" and not local)):
        raise ValueError("World Monitor URL must be an HTTPS origin or HTTP loopback origin")
    return base.rstrip("/")


def parse_digest(payload: dict, lookback: float, limit: int, now: datetime | None = None) -> list[DeskResult]:
    if not isinstance(payload, dict) or not isinstance(payload.get("categories"), dict):
        raise ValueError("invalid World Monitor digest")
    now = now or datetime.now(timezone.utc)
    results: dict[str, DeskResult] = {}
    skipped = 0
    for category, bucket in sorted(payload["categories"].items()):
        if not isinstance(bucket, dict) or not isinstance(bucket.get("items"), list):
            raise ValueError("invalid World Monitor category")
        desk = category if category in DESK_TITLES else {"technology": "tech", "finance": "business"}.get(category, "world")
        result = results.setdefault(desk, DeskResult(desk, []))
        for row in bucket["items"][:1000]:
            try:
                if not isinstance(row, dict):
                    raise ValueError()
                url = canonical_url(str(row.get("link") or ""))
                p = urlsplit(url)
                if p.scheme not in ("http", "https") or not p.hostname:
                    raise ValueError()
                title = strip_html(str(row.get("title") or ""))[:2000]
                source = strip_html(str(row.get("publisherName") or row.get("originPublisher") or row.get("source") or ""))[:200]
                if not title or not source:
                    raise ValueError()
                # Wire contract uses camelCase fields and epoch milliseconds.
                published = datetime.fromtimestamp(float(row["publishedAt"]) / 1000, timezone.utc)
                age = (now - published).total_seconds()
                if age > lookback * 3600 or age < -300:
                    result.skipped_old += 1
                    continue
                metadata = {k: row[k] for k in ("location", "locationName", "threat", "importanceScore", "credibilityScore", "credibilityUnassessed", "corroborationCount", "storyMeta", "tickers", "linkVerified") if k in row}
                metadata.update({"category": category, "generated_at": payload.get("generatedAt", ""), "coverage": payload.get("coverage", {})})
                tag_list = tags(desk, title)
                text, chars = tweet(title, url, tag_list)
                result.articles.append(Article(article_id(url, title), desk, title, url, source, iso(published), strip_html(str(row.get("snippet") or ""))[:2000], text, chars, tag_list, {"provider": "worldmonitor", "metadata": metadata}))
            except (ValueError, KeyError, TypeError, OverflowError, OSError):
                skipped += 1
    for result in results.values():
        result.articles.sort(key=lambda a: a.published or "", reverse=True)
        result.articles = result.articles[:limit]
    warnings = []
    coverage = payload.get("coverage", {})
    if isinstance(coverage, dict) and coverage.get("state") in ("partial", "stale", "unavailable"):
        warnings.append("World Monitor coverage: " + coverage["state"])
    if payload.get("feedStatuses"):
        warnings.append("World Monitor reports upstream feed gaps")
    if skipped:
        warnings.append(f"World Monitor skipped {skipped} invalid or undated items")
    if warnings:
        results.setdefault("world", DeskResult("world", [])).source_errors.extend(warnings)
    return list(results.values())


def fetch(lookback: float, limit: int) -> list[DeskResult]:
    if os.getenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "").lower() not in ("1", "true", "yes"):
        return []
    try:
        base = validate_base_url(os.getenv("AGENT_NEWSBOT_WORLDMONITOR_URL", "http://127.0.0.1:3000"))
        variant = os.getenv("AGENT_NEWSBOT_WORLDMONITOR_VARIANT", "full")
        if variant not in VARIANTS:
            raise ValueError("invalid variant")
        headers = {"Accept": "application/json"}
        key = os.getenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", "")
        if key:
            headers["X-WorldMonitor-Key"] = key
        # Never forward the API key through redirects. No retries within a fetch cycle.
        with requests.get(base + "/api/news/v1/list-feed-digest", params={"variant": variant, "lang": "en"}, headers=headers, timeout=(5, 15), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                return [DeskResult("world", [], source_errors=[f"World Monitor HTTP {response.status_code}"])]
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("oversized response")
            return parse_digest(json.loads(body), lookback, limit)
    except (requests.RequestException, ValueError, TypeError):
        # Do not log request URLs, credential-bearing headers, or response bodies.
        return [DeskResult("world", [], source_errors=["World Monitor unavailable or invalid response/configuration"])]
