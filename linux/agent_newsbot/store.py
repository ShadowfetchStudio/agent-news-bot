from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import HOME


def canonical_url(raw: str) -> str:
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return raw.lower()
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ocid"}
    ]
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    rebuilt = urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))
    return rebuilt


def article_id(url: str, fallback: str) -> str:
    digest = hashlib.sha256(canonical_url(url or fallback).encode()).hexdigest()
    return digest[:24]


def strip_html(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except Exception:
        pass
    cleaned = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


class FreshnessStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (HOME / "seen.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen (
                    id TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    desk TEXT,
                    seen_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_seen_at ON seen(seen_at);
                CREATE TABLE IF NOT EXISTS quotes (
                    symbol TEXT PRIMARY KEY,
                    payload TEXT,
                    updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS feed_cache (
                    url TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    updated_at REAL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def has_seen(self, item_id: str) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT 1 FROM seen WHERE id = ? LIMIT 1", (item_id,)).fetchone()
            return row is not None

    def mark(self, item_id: str, url: str, title: str, desk: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO seen(id, url, title, desk, seen_at) VALUES (?,?,?,?,?)",
                (item_id, url, title, desk, datetime.now(timezone.utc).timestamp()),
            )
            db.commit()

    def last_quote(self, symbol: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload FROM quotes WHERE symbol = ?", (symbol,)).fetchone()
            return row[0] if row else None

    def save_quote(self, symbol: str, payload: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO quotes(symbol, payload, updated_at) VALUES (?,?,?)",
                (symbol, payload, datetime.now(timezone.utc).timestamp()),
            )
            db.commit()

    def within_lookback(self, published: datetime | None, hours: float) -> bool:
        if published is None:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published >= cutoff

    def prune_seen(self, older_than_days: float = 30.0) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86_400
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
            db.commit()
            return cur.rowcount

    def feed_cache(self, url: str) -> tuple[str | None, str | None]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT etag, last_modified FROM feed_cache WHERE url = ?", (url,)).fetchone()
            if not row:
                return None, None
            return (row[0] or None), (row[1] or None)

    def save_feed_cache(self, url: str, etag: str | None, last_modified: str | None) -> None:
        if not etag and not last_modified:
            return
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO feed_cache(url, etag, last_modified, updated_at) VALUES (?,?,?,?)",
                (url, etag or "", last_modified or "", datetime.now(timezone.utc).timestamp()),
            )
            db.commit()


_TITLE_STOP = {
    "the", "and", "for", "that", "with", "from", "this", "have", "will",
    "are", "was", "were", "has", "had", "into", "over", "after", "says", "said",
}


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (title or "").lower())
    return {w for w in words if w not in _TITLE_STOP}


def is_near_duplicate_title(a: set[str], b: set[str], threshold: float = 0.72) -> bool:
    if len(a) < 4 or len(b) < 4:
        return False
    union = a | b
    return bool(union) and (len(a & b) / len(union)) >= threshold
