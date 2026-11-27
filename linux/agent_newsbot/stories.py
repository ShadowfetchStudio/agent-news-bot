"""Durable story revisions and at-least-once delivery to independent consumers."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .store import canonical_url, title_tokens, is_near_duplicate_title, article_id, can_group_titles
from .hashtags import tweet
from .library import LibraryMixin, SCHEMA
from .group_repairs import GroupRepairMixin, REPAIR_SCHEMA
from .retention import SCHEMA as RETENTION_SCHEMA, retained_guard
from .consumers import ConsumerMixin, CONSUMER_SCHEMA, delivery_guard
from .private_files import private_database
from .url_safety import safe_article_url
import time


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


CONTEXT_FIELDS = ("location", "locationName", "threat", "tickers", "linkVerified", "credibilityUnassessed")


def editorial_changed(sources, previous):
    if [(s["url"], s["title"], s["summary"]) for s in sources] != [(s["url"], s["title"], s["summary"]) for s in previous]:
        return True
    old = {s["url"]: s.get("observations", {}) for s in previous}
    for source in sources:
        before = old.get(source["url"], {})
        after = source.get("observations", {})
        for provider in before.keys() & after.keys():
            if any(before[provider].get(k) != after[provider].get(k) for k in ("title", "summary")):
                return True
    return False


def context_key(story):
    return {"desks": story.get("desks", [story.get("desk")]), "sources": [
        {"url": s["url"], "providers": s.get("providers", []),
         "context": {k: s.get("metadata", {}).get(k) for k in CONTEXT_FIELDS}}
        for s in story.get("sources", [])]}


def reports_disagree(sources):
    negative = {"not", "never", "no", "cancel", "cancels", "cancelled", "canceled", "denies", "rejected"}
    titles = []
    for source in sources:
        variants = {o.get("title", "").lower().strip() for o in source.get("observations", {}).values()}
        if len(variants) > 1:
            return True
        titles.append(source["title"])
    for i, a in enumerate(titles):
        for b in titles[i+1:]:
            if bool(set(a.lower().split()) & negative) != bool(set(b.lower().split()) & negative):
                if is_near_duplicate_title(title_tokens(a), title_tokens(b), .5):
                    return True
    return False


class StoryStore(LibraryMixin, ConsumerMixin, GroupRepairMixin):
    def __init__(self, path: Path):
        self.path = path
        private_database(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS stories_updated ON stories(updated);
                CREATE TABLE IF NOT EXISTS members(url TEXT PRIMARY KEY, story_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS consumers(name TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS events_story_seq ON events(story_id,seq);
                CREATE TABLE IF NOT EXISTS identity_aliases(alias TEXT PRIMARY KEY, story_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);
            ''')

            for sql in SCHEMA: db.execute(sql)
            db.execute(CONSUMER_SCHEMA)
            db.execute(RETENTION_SCHEMA)
            db.execute(REPAIR_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM migrations WHERE name='canonical-v2'").fetchone():
                for url, story_id in db.execute("SELECT url,story_id FROM members ORDER BY url").fetchall():
                    normalized = canonical_url(url)
                    db.execute("INSERT OR IGNORE INTO members VALUES (?,?)", (normalized, story_id))
                    db.execute("INSERT OR IGNORE INTO identity_aliases VALUES (?,?)", (article_id(normalized, ""), story_id))
                db.execute("INSERT INTO migrations VALUES ('canonical-v2')")

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
        if any(not safe_article_url(a['url']) for a in articles): raise ValueError('invalid article URL')
        # Group first, then emit one complete revision per story per transaction.
        unique, desks_by_url = {}, {}
        for a in articles:
            url = canonical_url(a["url"])
            desks_by_url.setdefault(url, set()).add(a["desk"])
            key = (a.get("extra", {}).get("provider", "direct"), url)
            rank = lambda item: (len(item.get("summary", "")), item["title"], item.get("summary", ""), item["source"], item["desk"])
            if key not in unique or rank(a) > rank(unique[key]):
                unique[key] = a
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            baseline, current = {}, {}
            for key in sorted(unique):
                article = unique[key]
                provider, url = key
                row = db.execute("SELECT s.payload FROM members m JOIN stories s ON s.id=m.story_id WHERE m.url=?", (url,)).fetchone()
                old = json.loads(row[0]) if row else None
                if old is None and article["desk"] not in ("weather", "markets", "polymarket"):
                    for candidate, in db.execute("SELECT payload FROM stories WHERE updated>=? ORDER BY updated DESC LIMIT 500", (now.timestamp() - 48*3600,)):
                        candidate = json.loads(candidate)
                        if not candidate.get("grouping_locked") and not candidate.get("merged_into") and candidate["desk"] not in ("weather", "markets", "polymarket") and can_group_titles(article["title"], candidate["title"]):
                            old = candidate
                            break
                story_id = old["id"] if old else article["id"]
                if story_id not in baseline:
                    baseline[story_id] = old
                original = baseline[story_id]
                version = (original or {}).get("observation_version", (original or {}).get("revision", 0)) + 1
                sources = {canonical_url(s["url"]): dict(s, url=canonical_url(s["url"])) for s in old.get("sources", [])} if old else {}
                prior = sources.get(url, {})
                observations = dict(prior.get("observations", {}))
                if not observations and prior:
                    for name in prior.get("providers", ["direct"]):
                        observations[name] = {k: prior.get(k, "") for k in ("title", "summary", "source", "published")}
                        observations[name].update(changed_version=0, legacy_imported=True)
                extra = article.get("extra", {})
                observation = {k: article.get(k, "") for k in ("title", "summary", "source", "published")}
                before = observations.get(provider)
                editorial_change = before is None or any(before.get(k) != observation[k] for k in ("title", "summary"))
                observation.update(metadata=extra.get("metadata", {}), last_seen=stamp,
                                   changed_at=stamp if editorial_change else before.get("changed_at", stamp),
                                   changed_version=version if editorial_change else before.get("changed_version", 0))
                observations[provider] = observation
                # A new substantive observation outranks old text. Equal-cycle ties prefer
                # direct reporting; a longer summary only wins for the same headline.
                winner = max(observations, key=lambda p: (observations[p].get("changed_version", 0), p == "direct", p))
                chosen = observations[winner]
                summary = max((o.get("summary", "") for o in observations.values() if o["title"] == chosen["title"]), key=len, default="")
                metadata = observations.get("worldmonitor", {}).get("metadata") or chosen.get("metadata") or prior.get("metadata", {})
                sources[url] = dict(chosen, url=url, summary=summary, providers=sorted(observations),
                                    selected_provider=winner, observations=observations, metadata=metadata)
                story = dict(old or article)
                story.setdefault('grouping_policy', 'legacy-title-overlap' if old else 'equivalent-wording-v1')
                story.pop("extra", None)
                story.update(id=story_id, story_id=story_id, url=canonical_url(story["url"]),
                             sources=[sources[k] for k in sorted(sources)], observation_version=version)
                story["desks"] = sorted(set(story.get("desks", [story["desk"]])) | desks_by_url[url])
                main = sources.get(story["url"], sources[url])
                for k in ("title", "summary", "source", "published"):
                    story[k] = main.get(k, story.get(k))
                story["tweet"], story["tweet_chars"] = tweet(story["title"], story["url"], story.get("hashtags", []))
                story["first_seen"] = (old or {}).get("first_seen", extra.get("observed_at") or stamp)
                story["disagreement"] = reports_disagree(story["sources"])
                story["review_note"] = "Reports differ; review source observations before use." if story["disagreement"] else ""
                db.execute("INSERT OR REPLACE INTO stories VALUES (?,?,?)", (story_id, encoded(story), now.timestamp()))
                db.execute("INSERT OR REPLACE INTO members VALUES (?,?)", (url, story_id))
                db.execute("INSERT OR IGNORE INTO identity_aliases VALUES (?,?)", (article["id"], story_id))
                current[story_id] = story
            changed = []
            for story_id, story in current.items():
                original = baseline[story_id]
                editorial = original is None or editorial_changed(story["sources"], original["sources"])
                enrichment = original is not None and context_key(story) != context_key(original)
                if editorial or enrichment:
                    story["revision"] = (original or {}).get("revision", 0) + 1
                    story["change"] = "new" if original is None else "updated" if editorial else "enriched"
                    story["updated_at"] = stamp
                    fields = ("title", "summary", "sources", "desks", "disagreement")
                    story["changed_fields"] = [k for k in fields if (original or {}).get(k) != story.get(k)]
                    cur = db.execute("INSERT INTO events(story_id,payload) VALUES (?,?)", (story_id, encoded(story)))
                    story["event_seq"] = cur.lastrowid
                    changed.append(story)
                db.execute("INSERT OR REPLACE INTO stories VALUES (?,?,?)", (story_id, encoded(story), now.timestamp()))
            return changed

    def story(self, story_id: str) -> dict | None:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT payload FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", (story_id, story_id)).fetchone()
            return json.loads(row[0]) if row else None

    def revisions(self, story_id: str, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1-100")
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT seq,payload FROM events WHERE story_id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?)) ORDER BY seq DESC LIMIT ?", (story_id, story_id, limit + 1)).fetchall()[::-1]
        previous, result = {}, []
        for seq, raw in rows:
            story = json.loads(raw)
            changes = {k: {"before": previous.get(k), "after": story.get(k)} for k in ("title", "summary", "sources", "desks", "disagreement") if previous.get(k) != story.get(k)}
            gap = story.get("revision", 0) > previous.get("revision", 0) + 1
            result.append({"seq": seq, "story": story, "diff": {} if gap else changes,
                           "history_gap_before": gap})
            previous = story
        return result[-limit:]

    @staticmethod
    def validate_consumer(name):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("consumer must be 1-64 letters, digits, dots, underscores or hyphens")

    def pending(self, consumer: str, limit: int = 100, lease=None) -> dict:
        self.validate_consumer(consumer)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1-500")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO consumers(name) VALUES (?)", (consumer,))
            generation = delivery_guard(db, consumer, lease=lease)
            cursor, offered = db.execute("SELECT cursor,offered FROM consumers WHERE name=?", (consumer,)).fetchone()
            floor = retained_guard(db, cursor)
            rows = db.execute("SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", (cursor, limit)).fetchall()
            end = rows[-1][0] if rows else cursor
            db.execute("UPDATE consumers SET offered=? WHERE name=?", (max(offered, end), consumer))
            events = [{"seq": seq, "story": json.loads(payload)} for seq, payload in rows]
            return {"consumer": consumer, "generation": generation, "cursor": cursor, "next_cursor": end, "count": len(events), "events": events, "pruned_through": floor}

    def acknowledge(self, consumer: str, through: int, generation=None, lease=None) -> dict:
        self.validate_consumer(consumer)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            generation = delivery_guard(db, consumer, generation, lease, ack=True)
            row = db.execute("SELECT cursor,offered FROM consumers WHERE name=?", (consumer,)).fetchone()
            if row is None or through < 0 or through > row[1]:
                raise ValueError("cannot acknowledge events not delivered to this consumer")
            retained_guard(db, row[0])
            cursor = max(row[0], through)
            db.execute("UPDATE consumers SET cursor=? WHERE name=?", (cursor, consumer))
            db.execute("UPDATE consumer_meta SET last_ack=? WHERE name=?", (time.time(), consumer))
            return {"consumer": consumer, "cursor": cursor, "generation": generation}
