"""Durable story revisions and at-least-once delivery to independent consumers."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .store import canonical_url, title_tokens, is_near_duplicate_title


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_key(sources):
    # Provider scores, timestamps and lifecycle counters are not editorial updates.
    return [(s["url"], s["title"], s["summary"]) for s in sources]


class StoryStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS stories_updated ON stories(updated);
                CREATE TABLE IF NOT EXISTS members(url TEXT PRIMARY KEY, story_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS consumers(name TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, articles: list[dict]) -> list[dict]:
        # Multiple desks may carry different snippets for the same URL. Pick one
        # deterministic representation per provider before comparing with history.
        unique = {}
        for a in articles:
            key = (a.get("extra", {}).get("provider", "direct"), canonical_url(a["url"]))
            rank = lambda item: (len(item.get("summary", "")), item["title"], item.get("summary", ""), item["source"], item["desk"])
            if key not in unique or rank(a) > rank(unique[key]):
                unique[key] = a
        articles = [unique[key] for key in sorted(unique)]
        changed: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for article in articles:
                url = canonical_url(article["url"])
                row = db.execute("SELECT s.payload FROM members m JOIN stories s ON s.id=m.story_id WHERE m.url=?", (url,)).fetchone()
                old = json.loads(row[0]) if row else None
                if old is None and article["desk"] not in ("weather", "markets", "polymarket"):
                    # Bounded candidate window; this is heuristic grouping, not fact verification.
                    tokens = title_tokens(article["title"])
                    for candidate, in db.execute("SELECT payload FROM stories WHERE updated>=? ORDER BY updated DESC LIMIT 500", (now.timestamp() - 48*3600,)):
                        candidate = json.loads(candidate)
                        if candidate["desk"] in ("weather", "markets", "polymarket"):
                            continue
                        if is_near_duplicate_title(tokens, title_tokens(candidate["title"])):
                            old = candidate
                            break
                extra = article.get("extra", {})
                source = {"url": url, "title": article["title"], "source": article["source"], "published": article.get("published", ""), "summary": article.get("summary", ""), "providers": [extra.get("provider", "direct")], "metadata": extra.get("metadata", {})}
                sources = {s["url"]: s for s in old["sources"]} if old else {}
                prior = sources.get(url)
                if prior:
                    source["providers"] = sorted(set(prior["providers"] + source["providers"]))
                    if not source["metadata"]:
                        source["metadata"] = prior["metadata"]
                    # A digest's short snippet must not oscillate with a direct RSS summary.
                    if extra.get("provider") == "worldmonitor" and "direct" in prior["providers"]:
                        source["summary"] = prior["summary"]
                        source["title"] = prior["title"]
                sources[url] = source
                ordered = [sources[k] for k in sorted(sources)]
                substantive = old is None or content_key(ordered) != content_key(old["sources"])
                story = dict(old or article)
                story.pop("extra", None)
                story_id = old["id"] if old else article["id"]
                story.update(id=story_id, story_id=story_id, sources=ordered)
                if url == story["url"] and not (extra.get("provider") == "worldmonitor" and prior and "direct" in prior["providers"]):
                    for k in ("title", "summary", "tweet", "tweet_chars", "hashtags", "published"):
                        story[k] = article.get(k, story.get(k))
                story["first_seen"] = old["first_seen"] if old else now.isoformat()
                if substantive:
                    story["revision"] = (old["revision"] if old else 0) + 1
                    story["change"] = "new" if old is None else "updated"
                    story["updated_at"] = now.isoformat()
                db.execute("INSERT OR REPLACE INTO stories VALUES (?,?,?)", (story_id, encoded(story), now.timestamp()))
                db.execute("INSERT OR REPLACE INTO members VALUES (?,?)", (url, story_id))
                if substantive:
                    cur = db.execute("INSERT INTO events(story_id,payload) VALUES (?,?)", (story_id, encoded(story)))
                    story["event_seq"] = cur.lastrowid
                    changed[story_id] = story
            return list(changed.values())

    @staticmethod
    def validate_consumer(name):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("consumer must be 1-64 letters, digits, dots, underscores or hyphens")

    def pending(self, consumer: str, limit: int = 100) -> dict:
        self.validate_consumer(consumer)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1-500")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO consumers(name) VALUES (?)", (consumer,))
            cursor, offered = db.execute("SELECT cursor,offered FROM consumers WHERE name=?", (consumer,)).fetchone()
            rows = db.execute("SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", (cursor, limit)).fetchall()
            end = rows[-1][0] if rows else cursor
            db.execute("UPDATE consumers SET offered=? WHERE name=?", (max(offered, end), consumer))
            events = [{"seq": seq, "story": json.loads(payload)} for seq, payload in rows]
            return {"consumer": consumer, "cursor": cursor, "next_cursor": end, "count": len(events), "events": events}

    def acknowledge(self, consumer: str, through: int) -> dict:
        self.validate_consumer(consumer)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT cursor,offered FROM consumers WHERE name=?", (consumer,)).fetchone()
            if row is None or through < 0 or through > row[1]:
                raise ValueError("cannot acknowledge events not delivered to this consumer")
            cursor = max(row[0], through)
            db.execute("UPDATE consumers SET cursor=? WHERE name=?", (cursor, consumer))
            return {"consumer": consumer, "cursor": cursor}
