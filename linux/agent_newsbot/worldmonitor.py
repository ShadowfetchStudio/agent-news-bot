"""Optional World Monitor REST adapter; no platform code is embedded.

Contract: proto/worldmonitor/news/v1/{service,list_feed_digest,news_item}.proto.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import base64
from pathlib import Path
import subprocess
import sys

from .coverage import check
from .url_safety import safe_article_url
from .config import Article, DeskResult, DESK_TITLES
from .hashtags import tags, tweet
from .store import article_id, canonical_url, iso, strip_html

VARIANTS = {"full", "tech", "finance", "happy", "commodity", "energy"}
MAX_BYTES = 4 * 1024 * 1024
HTTP_DEADLINE = 20.0


class DiagnosticError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def diagnosis(code):
    messages = {
        "configuration": "Use an HTTPS server origin, or HTTP on localhost, with no path, query or credentials. Check the edition and API key format.",
        "schema": "The server did not return a compatible news digest. Check its API version and origin.",
        "response_too_large": "The digest exceeds the 4 MiB limit. Choose a narrower edition or contact the provider.",
        "timeout": "World Monitor exceeded its request deadline. Check the server and retry later.",
        "tls": "World Monitor TLS validation failed. Fix the server certificate; verification remains enabled.",
        "network": "Cannot reach World Monitor. Check the server, network and port.",
    }
    if code.startswith("http_"):
        status = int(code[5:])
        detail = {401: "authentication failed. Check the API key.",
                  403: "access denied. Check your account or server permissions.",
                  404: "digest endpoint not found. Check the server origin and API compatibility.",
                  429: "rate limited. Wait before retrying and check the provider's quota."}.get(status)
        if detail is None:
            detail = ("redirect refused to protect your key. Enter the final authorized server origin." if 300 <= status < 400 else
                      "provider unavailable. Retry later; direct sources remain independent." if 500 <= status < 600 else
                      "request rejected. Check the provider configuration.")
        message = f"World Monitor HTTP {status}: {detail}"
    else:
        if code not in messages: code = "network"
        message = messages[code]
    return {"code": code, "message": message, "succeeded": False}


def retrieve(base, variant, key, lookback=12, limit=20):
    try:
        base = validate_base_url(base)
        if variant not in VARIANTS or len(base.encode()) > 4096 or len(key.encode()) > 8192 or any(not 32 <= ord(c) <= 126 for c in key):
            raise ValueError()
    except ValueError:
        raise DiagnosticError("configuration") from None
    try:
        status, payload = bounded_request(base, variant, key)
        if status != 200: raise DiagnosticError(f"http_{status}")
        try: return parse_digest(payload, lookback, limit)
        except (ValueError, TypeError, KeyError): raise DiagnosticError("schema") from None
    except subprocess.TimeoutExpired: raise DiagnosticError("timeout") from None
    except DiagnosticError: raise
    except (OSError, subprocess.SubprocessError): raise DiagnosticError("network") from None
    except (ValueError, KeyError, TypeError): raise DiagnosticError("schema") from None


def test_connection(base, variant, key):
    from .coverage import summary
    try:
        results = retrieve(base, variant, key)
        state = summary([c for result in results for c in result.source_checks])["state"]
        return {"code": "connected", "succeeded": True,
                "message": f"Compatible digest received. Coverage: {state}. This checks access and format, not factual accuracy or content rights. No stories saved."}
    except DiagnosticError as error: return diagnosis(error.code)


def bounded_request(base, variant, key):
    if len(key)>8192 or len(base)>4096: raise ValueError("configuration too long")
    request=json.dumps({"url":base+"/api/news/v1/list-feed-digest","variant":variant,"key":key}).encode()
    env=dict(os.environ); env.pop("AGENT_NEWSBOT_WORLDMONITOR_KEY",None)
    worker=Path(__file__).with_name("worldmonitor_worker.py")
    result=subprocess.run([sys.executable,"-I",str(worker)],input=request,capture_output=True,timeout=HTTP_DEADLINE,env=env)
    if result.returncode!=0 or len(result.stdout)>MAX_BYTES*2: raise ValueError("request worker failed")
    response=json.loads(result.stdout)
    if "error" in response: raise DiagnosticError(response["error"] if response["error"] in ("tls", "timeout", "response_too_large") else "network")
    if response.get("status")!=200: return response.get("status"),None
    body=base64.b64decode(response["body"],validate=True)
    if len(body)>MAX_BYTES: raise DiagnosticError("response_too_large")
    return 200,json.loads(body)


def validate_base_url(base: str) -> str:
    if not safe_article_url(base): raise ValueError("invalid origin")
    p = urlsplit(base)
    if p.port is not None and not 1 <= p.port <= 65535: raise ValueError("invalid port")
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
                raw_link = str(row.get("link") or "")
                if not safe_article_url(raw_link): raise ValueError("invalid article URL")
                url = canonical_url(raw_link)
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
    reported = coverage.get("state", "complete") if isinstance(coverage, dict) else "complete"
    state = "success" if reported == "complete" else reported
    if state not in ("success", "partial", "stale", "unavailable") or (state == "success" and warnings):
        state = "partial"
    if not results:
        results["world"] = DeskResult("world", [])
    for result in results.values():
        result.source_checks = [check("World Monitor digest", state, "; ".join(warnings), "worldmonitor")]
    return list(results.values())


def fetch(lookback: float, limit: int) -> list[DeskResult]:
    if os.getenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "").lower() not in ("1", "true", "yes"):
        return []
    try:
        return retrieve(os.getenv("AGENT_NEWSBOT_WORLDMONITOR_URL", "http://127.0.0.1:3000"),
                        os.getenv("AGENT_NEWSBOT_WORLDMONITOR_VARIANT", "full"),
                        os.getenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", ""), lookback, limit)
    except DiagnosticError as error:
        message = diagnosis(error.code)["message"]
        return [DeskResult("world", [], source_errors=[message], source_checks=[check("World Monitor digest", "unavailable", message, "worldmonitor")])]
