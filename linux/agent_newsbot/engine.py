from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import feedparser

from . import hashtags, worldmonitor
from .stories import StoryStore
from .url_safety import safe_article_url
from . import coverage
from . import http_client
from .config import (
    ALL_DESKS,
    Article,
    DESK_TITLES,
    DeskResult,
    LOOKBACK_HOURS,
    MARKET_SYMBOLS,
    MAX_PER_DESK,
    RSS_FEEDS,
    SEEN_RETENTION_DAYS,
    USER_AGENT,
    WEATHER_CITY,
)
from .store import (
    FreshnessStore,
    article_id,
    canonical_url,
    iso,
    parse_date,
    strip_html,
)

log = logging.getLogger("agent_newsbot")


def _get(
    url: str,
    accept: str = "*/*",
    timeout: int = 14,
    extra_headers: dict[str, str] | None = None,
) -> http_client.Response:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    return http_client.get(url, timeout=timeout, headers=headers)


def fetch_rss(desk: str, store: FreshnessStore, lookback: float, limit: int) -> DeskResult:
    collected: list[Article] = []
    errors: list[str] = []
    skipped_old = 0
    skipped_dup = 0
    seen_urls: set[str] = set()
    cache_updates = []
    checks = []
    for url, label in RSS_FEEDS.get(desk, []):
        try:
            etag, last_mod = store.feed_cache(url)
            document = store.feed_document(url)
            if document is None:
                etag, last_mod = None, None
            cond: dict[str, str] = {}
            if etag:
                cond["If-None-Match"] = etag
            if last_mod:
                cond["If-Modified-Since"] = last_mod
            resp = _get(
                url,
                accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                extra_headers=cond,
            )
            if resp.status_code == 304:
                if document is None:
                    raise ValueError("304 response without cached feed")
                body, observed_at = document
            else:
                resp.raise_for_status()
                body = resp.content
                observed_at = document[1] if document and document[0] == body else datetime.now(timezone.utc).timestamp()
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("feed exceeds 4 MiB")
            parsed = feedparser.parse(body)
            if not parsed.version:
                raise ValueError("invalid feed")
            checks.append(coverage.check(label))
            if resp.status_code != 304:
                cache_updates.append((url, resp.headers.get("ETag"), resp.headers.get("Last-Modified"), body, observed_at))
            for entry in parsed.entries:
                raw_link = getattr(entry, "link", "") or ""
                if not safe_article_url(raw_link):
                    errors.append(f"{label}: skipped a non-web or invalid article link")
                    checks.append(coverage.check(label, "partial", "Invalid article URL skipped"))
                    continue
                link = canonical_url(raw_link)
                if not link or "news.google.com" in link:
                    continue
                if link in seen_urls:
                    skipped_dup += 1
                    continue
                seen_urls.add(link)
                published = parse_date(
                    getattr(entry, "published", None)
                    or getattr(entry, "updated", None)
                    or getattr(entry, "created", None)
                )
                if not store.within_lookback(published or datetime.fromtimestamp(observed_at, timezone.utc), lookback):
                    skipped_old += 1
                    continue
                title = strip_html(getattr(entry, "title", "") or "")
                if not title:
                    continue
                item_id = article_id(link, title)
                summary = strip_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
                tag_list = hashtags.tags(desk, title)
                tweet, chars = hashtags.tweet(title, link, tag_list)
                collected.append(
                    Article(
                        id=item_id,
                        desk=desk,
                        title=title,
                        url=link,
                        source=label,
                        published=iso(published),
                        summary=summary,
                        tweet=tweet,
                        tweet_chars=chars,
                        hashtags=tag_list,
                        extra={"observed_at": iso(datetime.fromtimestamp(observed_at, timezone.utc))},
                    )
                )
        except Exception as exc:
            log.warning("feed failed %s %s: %s", desk, label, exc)
            errors.append(f"{label}: feed unreachable")
            checks.append(coverage.check(label, "unavailable", "Feed request or parsing failed"))
        time.sleep(0.12)
    collected.sort(key=lambda a: a.published or "", reverse=True)
    return DeskResult(desk, collected[:limit], skipped_old, skipped_dup, errors, cache_updates, source_checks=checks)


def fetch_hn(store: FreshnessStore, lookback: float, limit: int) -> DeskResult:
    errors: list[str] = []
    checks = []
    skipped_old = 0
    skipped_dup = 0
    articles: list[Article] = []
    try:
        newest = _get("https://hacker-news.firebaseio.com/v0/newstories.json", accept="application/json").json()
        top = _get("https://hacker-news.firebaseio.com/v0/topstories.json", accept="application/json").json()
        if not isinstance(newest, list) or not isinstance(top, list):
            raise ValueError("Invalid Hacker News indexes")
        checks.append(coverage.check("Hacker News indexes"))
        ordered: list[int] = []
        seen: set[int] = set()
        for hid in list(newest[:80]) + list(top[:40]):
            if hid not in seen:
                seen.add(hid)
                ordered.append(hid)
        cutoff = datetime.now(timezone.utc).timestamp() - lookback * 3600
        for hid in ordered:
            if len(articles) >= limit:
                break
            try:
                item = _get(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", accept="application/json", timeout=8).json()
            except Exception:
                errors.append("Hacker News: item request failed")
                continue
            if not item or item.get("dead") or item.get("deleted"):
                continue
            if item.get("type") not in (None, "story"):
                continue
            title = item.get("title") or ""
            if not title:
                continue
            published_ts = float(item.get("time") or 0)
            if published_ts < cutoff:
                skipped_old += 1
                continue
            url = item.get("url") or f"https://news.ycombinator.com/item?id={hid}"
            if not safe_article_url(url):
                errors.append("Hacker News: skipped invalid article link")
                continue
            if "news.google.com" in url:
                continue
            link = canonical_url(url)
            item_id = article_id(link, f"hn-{hid}")
            extra_tags = ["#Trending"] if (item.get("score") or 0) >= 100 else []
            tag_list = hashtags.tags("hackerNews", title, extra_tags)
            tweet, chars = hashtags.tweet(title, link, tag_list)
            articles.append(
                Article(
                    id=item_id,
                    desk="hackerNews",
                    title=title,
                    url=link,
                    source=f"HN · {item.get('by', 'anon')} · {item.get('score', 0)}",
                    published=iso(datetime.fromtimestamp(published_ts, tz=timezone.utc)),
                    summary=strip_html(item.get("text") or ""),
                    tweet=tweet,
                    tweet_chars=chars,
                    hashtags=tag_list,
                    extra={"hn": f"https://news.ycombinator.com/item?id={hid}", "score": str(item.get("score") or 0)},
                )
            )
    except Exception as exc:
        log.warning("hn failed: %s", exc)
        errors.append("Hacker News: API unreachable")
    articles.sort(key=lambda a: a.published or "", reverse=True)
    return DeskResult("hackerNews", articles, skipped_old, skipped_dup, errors, source_checks=checks)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_polymarket(store: FreshnessStore, lookback: float, limit: int) -> DeskResult:
    errors: list[str] = []
    checks = []
    skipped_dup = 0
    skipped_old = 0
    articles: list[Article] = []
    urls = [
        "https://gamma-api.polymarket.com/events?limit=40&active=true&closed=false&order=volume24hr&ascending=false",
        "https://gamma-api.polymarket.com/markets?limit=40&active=true&closed=false&order=volume24hr&ascending=false",
    ]
    for url in urls:
        try:
            rows = _get(url, accept="application/json").json()
            if not isinstance(rows, list):
                raise ValueError("Invalid prediction market response")
            checks.append(coverage.check("Polymarket " + url.split("?")[0].rsplit("/", 1)[-1]))
            for row in rows:
                title = str(row.get("title") or row.get("question") or row.get("slug") or "")
                if not title:
                    continue
                slug = str(row.get("slug") or "")
                page = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"
                item_id = article_id(page, slug or title)
                end = parse_date(str(row.get("endDate") or row.get("end_date_iso") or ""))
                now = datetime.now(timezone.utc)
                if end and end < now and not store.within_lookback(end, lookback):
                    skipped_old += 1
                    continue
                volume = _num(row.get("volume24hr") or row.get("volume"))
                summary_parts = []
                if volume > 0:
                    summary_parts.append(f"24h volume ${volume:.0f}")
                extra = ["#HotMarket"] if volume >= 100_000 else []
                tag_list = hashtags.tags("polymarket", title, extra)
                tweet, chars = hashtags.tweet(title, page, tag_list)
                articles.append(
                    Article(
                        id=item_id,
                        desk="polymarket",
                        title=title,
                        url=page,
                        source="Polymarket",
                        published=iso(parse_date(str(row.get("startDate") or row.get("createdAt") or "")) or now),
                        summary=" · ".join(summary_parts),
                        tweet=tweet,
                        tweet_chars=chars,
                        hashtags=tag_list,
                        extra={"volume": f"{volume:.0f}"},
                    )
                )
        except Exception as exc:
            log.warning("polymarket failed: %s", exc)
            errors.append("Polymarket: API unreachable")
            checks.append(coverage.check("Polymarket " + url.split("?")[0].rsplit("/", 1)[-1], "unavailable", "Request or parsing failed"))
    unique: list[Article] = []
    seen: set[str] = set()
    for article in articles:
        if article.id in seen:
            continue
        seen.add(article.id)
        unique.append(article)
    unique.sort(key=lambda a: float(a.extra.get("volume") or 0), reverse=True)
    return DeskResult("polymarket", unique[:limit], skipped_old, skipped_dup, errors, source_checks=checks)


SYMBOL_NAMES = {"^GSPC": "S&P 500", "^DJI": "Dow", "^IXIC": "Nasdaq"}


def fetch_markets(store: FreshnessStore, symbols: str, limit: int) -> DeskResult:
    quote_updates = []
    errors: list[str] = []
    checks = []
    skipped_dup = 0
    articles: list[Article] = []
    for symbol in [s.strip().upper() for s in symbols.split(",") if s.strip()]:
        encoded = quote(symbol, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=5d"
        try:
            payload = _get(url, accept="application/json", timeout=12).json()
            meta = payload["chart"]["result"][0]["meta"]
            price = _num(meta.get("regularMarketPrice"))
            prev = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
            if not 0 < price < float("inf"):
                raise ValueError("Invalid quote price")
            checks.append(coverage.check("Yahoo Finance " + symbol))
            change = (price - prev) / prev if prev else 0.0
            snapshot = f"{price:.4f}|{change:.5f}"
            last = store.last_quote(symbol)
            if last == snapshot:
                skipped_dup += 1
                continue
            if abs(change) < 0.0015 and last is not None:
                skipped_dup += 1
                continue
            quote_updates.append((symbol, snapshot))
            name = SYMBOL_NAMES.get(symbol) or meta.get("shortName") or meta.get("longName") or "Yahoo Finance"
            sign = "+" if change >= 0 else ""
            title = f"{name} {price:.2f} {sign}{change * 100:.2f}%"
            page = f"https://finance.yahoo.com/quote/{encoded}"
            item_id = article_id(page + snapshot, f"mkt-{symbol}-{snapshot}")
            cash = name.replace("^", "").replace(" ", "")
            tag_list = hashtags.tags("markets", title, [f"${cash}"])
            tweet, chars = hashtags.tweet(title, page, tag_list)
            currency = meta.get("currency") or "USD"
            articles.append(
                Article(
                    id=item_id,
                    desk="markets",
                    title=title,
                    url=page,
                    source=str(name),
                    published=iso(datetime.now(timezone.utc)),
                    summary=f"{currency} · prior close {prev:.2f}",
                    tweet=tweet,
                    tweet_chars=chars,
                    hashtags=tag_list,
                    extra={"symbol": symbol, "change": f"{change:.4f}"},
                )
            )
        except Exception as exc:
            log.warning("market %s failed: %s", symbol, exc)
            errors.append(f"{symbol}: quote failed")
            checks.append(coverage.check("Yahoo Finance " + symbol, "unavailable", "Quote request or parsing failed"))
    articles.sort(key=lambda a: abs(float(a.extra.get("change") or 0)), reverse=True)
    selected = articles[:limit]
    selected_symbols = {a.extra["symbol"] for a in selected}
    return DeskResult("markets", selected, 0, skipped_dup, errors,
                      quote_updates=[q for q in quote_updates if q[0] in selected_symbols], source_checks=checks)


WX = {
    0: "Clear",
    1: "Mostly clear",
    2: "Mostly clear",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    61: "Rain",
    63: "Rain",
    65: "Rain",
    71: "Snow",
    73: "Snow",
    75: "Snow",
    80: "Showers",
    81: "Showers",
    82: "Showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}


def fetch_weather(store: FreshnessStore, city: str) -> DeskResult:
    query = city.strip()
    if not query:
        return DeskResult("weather", [], 0, 0, ["Set AGENT_NEWSBOT_WEATHER_CITY"])
    try:
        geo = _get(
            f"https://geocoding-api.open-meteo.com/v1/search?name={quote(query)}&count=1&language=en&format=json",
            accept="application/json",
        ).json()
        first = (geo.get("results") or [None])[0]
        if not first:
            return DeskResult("weather", [], 0, 0, [f"Could not geocode {query}"])
        lat, lon = first["latitude"], first["longitude"]
        place = ", ".join(p for p in [first.get("name"), first.get("admin1"), first.get("country")] if p)
        wx = _get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min"
            "&forecast_days=2&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto",
            accept="application/json",
        ).json()
        current = wx.get("current") or {}
        if any(not isinstance(current.get(k), (int, float)) for k in ("temperature_2m", "weather_code", "apparent_temperature", "wind_speed_10m")):
            raise ValueError("Invalid weather observation")
        daily = wx.get("daily") or {}
        temp = _num(current.get("temperature_2m"))
        feel = _num(current.get("apparent_temperature"))
        wind = _num(current.get("wind_speed_10m"))
        code = int(_num(current.get("weather_code")))
        high = (daily.get("temperature_2m_max") or [temp])[0]
        low = (daily.get("temperature_2m_min") or [temp])[0]
        payload = f"{temp:.0f}|{code}|{high:.0f}"
        key = f"weather:{place}"
        if store.last_quote(key) == payload:
            return DeskResult("weather", [], 0, 1, [], source_checks=[coverage.check("Open-Meteo")])
        condition = WX.get(code, "Mixed")
        title = f"{place}: {int(temp)}°F {condition}"
        summary = f"Feels like {int(feel)}° · Wind {int(wind)} mph · Today {int(high)}/{int(low)}°F"
        page = f"https://open-meteo.com/en/forecast?latitude={lat}&longitude={lon}"
        item_id = article_id(page + payload, f"wx-{place}-{payload}")
        tag_list = hashtags.tags("weather", title, ["#Forecast"])
        tweet, chars = hashtags.tweet(title, page, tag_list)
        article = Article(
            id=item_id,
            desk="weather",
            title=title,
            url=page,
            source="Open-Meteo",
            published=iso(datetime.now(timezone.utc)),
            summary=summary,
            tweet=tweet,
            tweet_chars=chars,
            hashtags=tag_list,
            extra={"city": place},
        )
        return DeskResult("weather", [article], 0, 0, [], quote_updates=[(key, payload)], source_checks=[coverage.check("Open-Meteo")])
    except Exception as exc:
        log.warning("weather failed: %s", exc)
        return DeskResult("weather", [], 0, 0, ["Weather: Open-Meteo unreachable"])


def fetch_desk(desk: str, store: FreshnessStore) -> DeskResult:
    if desk == "hackerNews":
        return fetch_hn(store, LOOKBACK_HOURS, MAX_PER_DESK)
    if desk == "polymarket":
        return fetch_polymarket(store, LOOKBACK_HOURS, MAX_PER_DESK)
    if desk == "markets":
        return fetch_markets(store, MARKET_SYMBOLS, MAX_PER_DESK)
    if desk == "weather":
        return fetch_weather(store, WEATHER_CITY)
    return fetch_rss(desk, store, LOOKBACK_HOURS, MAX_PER_DESK)


def fetch_all(store: FreshnessStore) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    results: list[DeskResult] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_desk, desk, store): desk for desk in ALL_DESKS}
        for fut in as_completed(futures):
            results.append(fut.result())
    results.extend(worldmonitor.fetch(LOOKBACK_HOURS, MAX_PER_DESK))
    combined: dict[str, DeskResult] = {}
    for result in results:
        dest = combined.setdefault(result.desk, DeskResult(result.desk, []))
        dest.articles.extend(result.articles)
        dest.skipped_old += result.skipped_old
        dest.skipped_dup += result.skipped_dup
        dest.source_errors.extend(result.source_errors)
        dest.feed_cache_updates.extend(result.feed_cache_updates)
        dest.quote_updates.extend(result.quote_updates)
        dest.source_checks.extend(coverage.result_checks(result))
    results = sorted(combined.values(), key=lambda r: DESK_TITLES.get(r.desk, r.desk))
    candidates = [a for r in results for a in r.articles]
    journal = StoryStore(store.path.with_name("stories.sqlite"))
    articles = journal.ingest([a.as_dict() for a in candidates])
    for result in results:
        for update in result.feed_cache_updates:
            store.save_feed_cache(*update)
        for update in result.quote_updates:
            store.save_quote(*update)
    for article in candidates:
        store.mark(article.id, article.url, article.title, article.desk)
    store.prune_seen(older_than_days=SEEN_RETENTION_DAYS)
    articles.sort(key=lambda a: a.get("published") or "", reverse=True)
    scanned = sum(len(r.articles) + r.skipped_old + r.skipped_dup for r in results)
    return {
        "fetched_at": iso(started),
        "finished_at": iso(datetime.now(timezone.utc)),
        "lookback_hours": LOOKBACK_HOURS,
        "total_articles": len(articles),
        "scanned": scanned,
        "articles": articles,
        "coverage": coverage.summary([c for r in results for c in coverage.result_checks(r)]),
        "desks": [
            {
                "desk": r.desk,
                "title": DESK_TITLES.get(r.desk, r.desk),
                "fresh": sum(r.desk in a.get("desks", [a["desk"]]) for a in articles),
                "skipped_old": r.skipped_old,
                "skipped_dup": r.skipped_dup,
                "errors": r.source_errors,
                "coverage": coverage.summary(coverage.result_checks(r)),
                "new": sum(r.desk in a.get("desks", [a["desk"]]) and a.get("change") == "new" for a in articles),
                "updated": sum(r.desk in a.get("desks", [a["desk"]]) and a.get("change") != "new" for a in articles),
                "carried": 0,
                "displayed": sum(r.desk in a.get("desks", [a["desk"]]) for a in articles),
            }
            for r in results
        ],
        "categories": {
            DESK_TITLES.get(r.desk, r.desk): [a for a in articles if r.desk in a.get("desks", [a["desk"]])] for r in results
        },
    }
