from __future__ import annotations

import hmac
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
import os
import tempfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .config import FETCH_INTERVAL, HOME, LOOKBACK_HOURS, PORT, SHARED, MAX_PER_DESK, ALL_DESKS
from . import coverage
from .api_contract import capabilities, error_payload, allowed_methods, APP_VERSION
from .engine import fetch_all
from .store import FreshnessStore
from .stories import StoryStore
from .library import LibraryConflict
from .consumers import ConsumerConflict
from .url_safety import safe_article_url
from .bounded_http import BoundedHTTPServer, DeadlineHandlerMixin

log = logging.getLogger("agent_newsbot")


class FetchBusy(RuntimeError):
    pass


class NewsState:
    def __init__(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        SHARED.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store = FreshnessStore(HOME / "seen.sqlite")
        self.stories = StoryStore(HOME / "stories.sqlite")
        self.token_path = HOME / "export" / "bridge.token"
        self.latest_path = SHARED / "latest.json"
        self.token = self._load_or_create_token()
        self.snapshot: dict = {
            "fetched_at": None,
            "total_articles": 0,
            "articles": [],
            "desks": [],
            "categories": {},
        }
        self.lock = threading.Lock()
        self.fetching = False
        self.fetch_job: dict = {"state": "idle"}
        self._load_snapshot()

    def _load_snapshot(self) -> None:
        if self.latest_path.exists():
            try:
                if self.latest_path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("Snapshot exceeds 16 MiB")
                loaded = json.loads(self.latest_path.read_text())
                loaded["articles"] = self.stories.reconcile_groups(loaded.get("articles", []))
                # Restoring a snapshot must not renew its publication/observation date.
                empty = dict(loaded, articles=[], desks=[dict(d, fresh=0) for d in loaded.get("desks", [])])
                self.snapshot = self._keep_live_desks(loaded, empty)
                for desk in self.snapshot.get("desks", []):
                    checks = desk.get("coverage", {}).get("sources", [])
                    for check in checks:
                        if check.get("state") in ("success", "partial"):
                            check["state"] = "stale"
                        check["detail"] = "Restored snapshot; not checked in this session. " + check.get("detail", "")
                    desk["coverage"] = coverage.summary(checks)
                self.snapshot["coverage"] = coverage.summary([c for d in self.snapshot.get("desks", []) for c in d.get("coverage", {}).get("sources", [])])
            except Exception:
                pass

    def _load_or_create_token(self) -> str:
        path = self.token_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink(): raise ValueError("bridge token must be a regular file, not a symlink")
        if path.exists():
            if not path.is_file() or path.stat().st_size>8192: raise ValueError("invalid bridge token file")
            path.chmod(0o600)
            token = path.read_text().strip()
            if len(token) >= 16 and not any(c.isspace() for c in token):
                return token
            raise ValueError("invalid bridge token file; preserve it and repair explicitly")
        token = secrets.token_hex(32)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.bridge-token-',delete=False) as handle:
                temporary=handle.name; handle.write(token + "\n"); handle.flush(); os.fsync(handle.fileno())
            # Publish complete bytes without replacing another creator's credential.
            os.link(temporary,path)
        except FileExistsError:
            return self._load_or_create_token()
        finally:
            if temporary is not None: os.unlink(temporary)
        return token

    def _begin_refresh(self) -> dict:
        # Caller owns lock; periodic, manual and HTTP refreshes share this gate.
        if self.fetching:
            raise FetchBusy("fetch already running")
        self.fetching = True
        self.fetch_job = {
            "id": str(uuid.uuid4()), "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.snapshot

    def request_refresh(self) -> tuple[int, dict]:
        with self.lock:
            if self.fetching:
                return 409, {"error": "fetch already running", "fetching": True,
                             "fetch_job": dict(self.fetch_job)}
            previous = self._begin_refresh()
            accepted = dict(self.fetch_job)
        def work() -> None:
            try:
                self._run_refresh(previous)
            except Exception:
                log.exception("requested fetch failed")
        try:
            threading.Thread(target=work, daemon=True, name="requested-fetch").start()
        except RuntimeError:
            with self.lock:
                self.fetching = False
                self.fetch_job.update(state="failed", error="fetch worker unavailable",
                                      finished_at=datetime.now(timezone.utc).isoformat())
            return 503, {"error": "fetch worker unavailable", "fetching": False}
        return 202, {"accepted": True, "fetching": True, "fetch_job": accepted,
                     "status_url": "/v1/status"}

    def refresh(self) -> dict:
        with self.lock:
            previous = self._begin_refresh()
        return self._run_refresh(previous)

    def _run_refresh(self, previous: dict) -> dict:
        outcome: dict = {"state": "failed", "error": "fetch failed; inspect source and storage status"}
        try:
            fresh = fetch_all(self.store)
            previous = dict(previous, articles=self.stories.reconcile_groups(previous.get("articles", [])))
            fresh["articles"] = self.stories.reconcile_groups(fresh.get("articles", []))
            snapshot = self._keep_live_desks(previous, fresh)
            tmp = self.latest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snapshot, indent=2))
            tmp.chmod(0o600)
            tmp.replace(self.latest_path)
            with self.lock:
                self.snapshot = snapshot
            outcome = {"state": "completed", "total_articles": snapshot.get("total_articles", 0)}
            log.info("fresh fetch: %s articles", snapshot.get("total_articles"))
            return snapshot
        finally:
            with self.lock:
                self.fetch_job.update(outcome)
                self.fetch_job["finished_at"] = datetime.now(timezone.utc).isoformat()
                self.fetching = False

    def repair_group(self, change):
        # Repairs and refresh startup serialize. A running fetch must finish first.
        with self.lock:
            if self.fetching: raise LibraryConflict("fetch in progress; retry the repair after completion")
            receipt = self.stories.repair_group(change)
            warning = None
            try:
                previous = dict(self.snapshot, articles=self.stories.reconcile_groups(self.snapshot.get("articles", [])))
                empty = dict(previous, articles=[], desks=[dict(d) for d in previous.get("desks", [])])
                self.snapshot = self._keep_live_desks(previous, empty)
                self.snapshot["snapshot_error"] = None
                fd, temp = tempfile.mkstemp(prefix=".repair-snapshot-", dir=self.latest_path.parent)
                try:
                    with os.fdopen(fd, "w") as handle:
                        json.dump(self.snapshot, handle, indent=2); handle.flush(); os.fsync(handle.fileno())
                    os.replace(temp, self.latest_path)
                finally:
                    if os.path.exists(temp): os.unlink(temp)
            except (OSError, ValueError, sqlite3.Error):
                warning = "Repair saved in the journal; snapshot update failed. Retry the same operation ID to refresh it."
            self.snapshot["snapshot_error"] = warning
            return {"repair": receipt, "snapshot_error": warning}

    @staticmethod
    def _story_time(item: dict, batch_time: str | None) -> datetime | None:
        for value in (item.get("published"), item.get("first_seen"), batch_time):
            if not value:
                continue
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        return None

    def _keep_live_desks(self, previous: dict, snapshot: dict) -> dict:
        """Keep eligible unchanged desks, with immutable fallback dates and explicit counts."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=LOOKBACK_HOURS)
        def live(item: dict, batch: dict) -> bool:
            if not safe_article_url(item.get('url')): return False
            dt = self._story_time(item, batch.get("fetched_at"))
            return dt is not None and cutoff <= dt <= now + timedelta(minutes=5)
        enabled = {row["desk"] for row in snapshot.get("desks") or []} or set(ALL_DESKS)
        kept = [dict(item, carried=False) for item in snapshot.get("articles") or [] if live(item, snapshot)]
        seen = {item["id"] for item in kept}
        for item in previous.get("articles") or []:
            if not enabled.intersection(item.get("desks", [item.get("desk")])) or item.get("id") in seen:
                continue
            if live(item, previous):
                carried = dict(item, carried=True)
                if not carried.get("first_seen"):
                    carried["first_seen"] = self._story_time(item, previous.get("fetched_at")).isoformat()
                kept.append(carried)
                seen.add(item["id"])
        # Stable ID breaks equal-date ties identically to the native app.
        kept.sort(key=lambda item: item['id'])
        kept.sort(key=lambda item: self._story_time(item, snapshot.get("fetched_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        selected = []
        counts = dict.fromkeys(enabled, 0)
        for item in kept:
            display = sorted(desk for desk in set(item.get('desks', [item.get('desk')]))
                             if desk in enabled and counts[desk] < max(0, MAX_PER_DESK))
            if not display:
                continue
            item['display_desks'] = display
            selected.append(item)
            for desk in display:
                counts[desk] += 1
        kept = selected
        snapshot["articles"] = kept
        snapshot["total_articles"] = len(kept)
        categories: dict[str, list] = {}
        for item in kept:
            for desk_name in item['display_desks']:
                from .config import DESK_TITLES
                categories.setdefault(DESK_TITLES.get(desk_name, desk_name), []).append(item)
        snapshot["categories"] = categories
        for desk in snapshot.get("desks") or []:
            items = [item for item in kept if desk["desk"] in item['display_desks']]
            carried = sum(bool(item.get("carried")) for item in items)
            updated = sum(not item.get("carried") and item.get("change") in ("updated", "enriched", "corrected") for item in items)
            fresh = len(items) - carried
            desk.update(new=fresh - updated, updated=updated, carried=carried,
                        displayed=len(items), fresh=fresh)
        return snapshot


STATE = NewsState()


class Handler(DeadlineHandlerMixin, BaseHTTPRequestHandler):
    server_version = "AgentNewsBot/" + APP_VERSION

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("%s - " + fmt, self.address_string(), *args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(error_payload(status, payload), indent=2).encode()
        if len(body) > 16 * 1024 * 1024:
            status = 413
            body = json.dumps(error_payload(413, {"error": "response too large; reduce limit"})).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(body)
        except OSError:
            pass

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler parser/unsupported-method failures must also be JSON.
        self._send(code, {"error": "HTTP request rejected"})

    def _unsupported_method(self):
        path = urlparse(self.path).path
        if path != "/health" and not self._authorized():
            self._send(401, {"error": "bearer token required"}); return
        self._send(405 if allowed_methods(path) else 404, {"error": "method not allowed" if allowed_methods(path) else "not found"})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = _unsupported_method

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        alt = self.headers.get("X-Agent-Token", "")
        expected_bearer = f"Bearer {STATE.token}"
        # HTTP headers are decoded as Latin-1. compare_digest(str, str) raises
        # on non-ASCII input; bytes comparisons safely return a mismatch instead.
        return hmac.compare_digest(auth.encode('utf-8'), expected_bearer.encode('utf-8')) or hmac.compare_digest(alt.encode('utf-8'), STATE.token.encode('utf-8'))

    def _library(self, method, parsed):
        paths = {"/v1/library", "/v1/library/settings", "/v1/library/editorial", "/v1/library/export", "/v1/library/repairs", "/v1/consumers"}
        if parsed.path not in paths:
            self._send(404, {"error": "not found"}); return
        if method != "GET" and not (method == "POST" and parsed.path in {"/v1/library/settings", "/v1/library/editorial", "/v1/library/repairs", "/v1/consumers"}):
            self._send(405, {"error": "method not allowed"}); return
        try:
            query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length", "-1"))
                if self.headers.get("Transfer-Encoding") or not 0 <= length <= 65536:
                    raise ValueError("a JSON body with Content-Length up to 65536 is required")
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                if len(raw) != length: raise ValueError("incomplete request body")
                body = json.loads(raw)
                if not isinstance(body, dict): raise ValueError("JSON object required")
            if parsed.path == "/v1/consumers": result = STATE.stories.consumer_status() if method == "GET" else STATE.stories.consumer_change(body)
            elif parsed.path == "/v1/library/repairs":
                if method == "POST":
                    if query: raise ValueError("repair POST does not accept query parameters")
                    result = STATE.repair_group(body)
                else:
                    if set(query) != {"operation_id"}: raise ValueError("operation_id is required")
                    receipt = STATE.stories.repair_receipt(query["operation_id"])
                    if receipt is None: raise LookupError("repair not found")
                    result = {"repair": receipt}
            elif parsed.path == "/v1/library": result = STATE.stories.library(query)
            elif parsed.path == "/v1/library/settings":
                result = STATE.stories.library_settings(body.get("kind"), body.get("name"), body.get("value"), write=method == "POST")
            elif parsed.path == "/v1/library/export": result = STATE.stories.research_export(query.get("id", ""))
            else:
                story = STATE.stories.save_editorial(query.get("id", ""), body) if method == "POST" else STATE.stories.editorial(query.get("id", ""))
                if story is None: raise LookupError("story not found")
                result = {"story": story}
            self._send(200, result)
        except (LibraryConflict, ConsumerConflict) as exc: self._send(409, {"error": str(exc)})
        except LookupError: self._send(404, {"error": "consumer not found" if parsed.path == "/v1/consumers" else "story not found"})
        except (ValueError, TypeError, RecursionError, TimeoutError) as exc: self._send(400, {"error": str(exc)[:200]})
        except (sqlite3.Error, OSError): self._send(503, {"error": "story storage unavailable"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send(200, {"ok": True, "app": "Agent News Bot", "pid": os.getpid(), "bridge": f"http://127.0.0.1:{PORT}"})
            return
        if not self._authorized():
            self._send(401, {"error": "bearer token required"})
            return
        methods = allowed_methods(path)
        if methods and "GET" not in methods:
            self._send(405, {"error": "method not allowed"}); return
        if path == "/v1/capabilities":
            if parsed.query: self._send(400, {"error": "capabilities does not accept query parameters"})
            else: self._send(200, capabilities())
            return
        if path == "/v1/library" or path.startswith("/v1/library/") or path == "/v1/consumers":
            self._library("GET", parsed); return
        qs = parse_qs(parsed.query)
        if path.startswith("/v1/stories/"):
            parts = path.strip("/").split("/")
            if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "revisions"):
                self._send(404, {"error": "not found"}); return
            try:
                story = STATE.stories.story(parts[2])
                if story is None:
                    self._send(404, {"error": "story not found"})
                elif len(parts) == 4:
                    limit = int((qs.get("limit") or ["50"])[0])
                    self._send(200, {"story_id": parts[2], "revisions": STATE.stories.revisions(parts[2], limit)})
                else:
                    self._send(200, {"story": story})
            except ValueError:
                self._send(400, {"error": "limit must be 1-100"})
            except (sqlite3.Error, OSError):
                self._send(503, {"error": "story storage unavailable"})
            return
        if path == "/v1/events":
            try:
                if set(qs)-{'consumer','limit'}: raise ValueError('event filters are unsupported; process the complete offered range')
                consumer = (qs.get("consumer") or [""])[0]
                limit = int((qs.get("limit") or ["100"])[0])
                self._send(200, STATE.stories.pending(consumer, limit, lease=self.headers.get('X-Consumer-Lease')))
            except ConsumerConflict as exc:
                self._send(409, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except (sqlite3.Error, OSError):
                self._send(503, {"error": "story storage unavailable"})
            return
        if path == "/v1/events/ack":
            self._send(405, {"error": "method not allowed"})
            return
        if path == "/v1/fresh":
            with STATE.lock:
                articles = list(STATE.snapshot.get("articles") or [])
            desk = (qs.get("desk") or [None])[0]
            if desk:
                articles = [a for a in articles if desk in a.get("display_desks", a.get("desks", [a.get("desk")]))]
            raw_limit = (qs.get("limit") or ["100"])[0]
            try:
                limit = int(raw_limit)
                if limit < 0:
                    raise ValueError("negative limit")
            except ValueError:
                self._send(400, {"error": "invalid limit parameter"})
                return
            articles = articles[: min(limit, 500)]
            self._send(200, {"count": len(articles), "articles": articles})
            return
        if path.startswith("/v1/articles/"):
            target_id = path[len("/v1/articles/") :]
            with STATE.lock:
                articles = list(STATE.snapshot.get("articles") or [])
            found = next((a for a in articles if a.get("id") == target_id), None)
            if found is None:
                self._send(404, {"error": "article not found"})
                return
            self._send(200, {"article": found})
            return
        if path == "/v1/desks":
            with STATE.lock:
                desks = STATE.snapshot.get("desks") or []
            self._send(200, {"desks": desks})
            return
        if path == "/v1/status":
            with STATE.lock:
                snap = STATE.snapshot
                fetching = STATE.fetching
                job = dict(STATE.fetch_job)
            self._send(
                200,
                {
                    "ok": True,
                    "fresh": sum(not a.get("carried") for a in snap.get("articles", [])),
                    "displayed": snap.get("total_articles", 0),
                    "coverage": snap.get("coverage", {"state": "unknown"}),
                    "fetched_at": snap.get("fetched_at"),
                    "fetching": fetching,
                    "fetch_job": job,
                    "snapshot_error": snap.get("snapshot_error"),
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(405, {"error": "method not allowed"}); return
        if not self._authorized():
            self._send(401, {"error": "bearer token required"})
            return
        methods = allowed_methods(parsed.path)
        if methods and "POST" not in methods:
            self._send(405, {"error": "method not allowed"}); return
        if parsed.path == "/v1/library" or parsed.path.startswith("/v1/library/") or parsed.path == "/v1/consumers":
            self._library("POST", parsed); return
        if parsed.path == "/v1/events/ack":
            qs = parse_qs(parsed.query)
            try:
                if set(qs)-{'consumer','through','generation'}: raise ValueError('event filters are unsupported; process the complete offered range')
                consumer = (qs.get("consumer") or [""])[0]
                through = int((qs.get("through") or [""])[0])
                generation = int(qs['generation'][0]) if 'generation' in qs else None
                self._send(200, STATE.stories.acknowledge(consumer, through, generation=generation, lease=self.headers.get('X-Consumer-Lease')))
            except ConsumerConflict as exc:
                self._send(409, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except (sqlite3.Error, OSError):
                self._send(503, {"error": "story storage unavailable"})
            return
        if parsed.path == "/v1/events":
            self._send(405, {"error": "method not allowed"})
            return
        if parsed.path == "/v1/fetch":
            status, payload = STATE.request_refresh()
            self._send(status, payload)
            return
        self._send(404, {"error": "not found"})


def loop_fetch() -> None:
    latest = STATE.latest_path
    if latest.exists():
        age = time.time() - latest.stat().st_mtime
        if age < FETCH_INTERVAL:
            time.sleep(FETCH_INTERVAL - age)
    while True:
        try:
            STATE.refresh()
        except FetchBusy:
            pass
        except Exception:
            log.exception("periodic fetch failed")
        time.sleep(FETCH_INTERVAL)


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    httpd = BoundedHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=loop_fetch, daemon=True, name="fetch-loop").start()
    log.info("Agent News Bot listening on http://127.0.0.1:%s", PORT)
    httpd.serve_forever()
