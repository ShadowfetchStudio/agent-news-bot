import Foundation
import SQLite3

/// Durable revisions. Delivery is at-least-once, with a separate acknowledgement per consumer.
final class StoryStore: @unchecked Sendable {
    private let path: URL
    private let lock = NSLock()
    init(path: URL) { self.path = path }
    enum Failure: Error { case database, invalidConsumer, invalidLimit, invalidAcknowledgement }

    private func withDB<T>(_ work: (OpaquePointer) throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        var handle: OpaquePointer?
        guard sqlite3_open(path.path, &handle) == SQLITE_OK, let db = handle else {
            if let handle { sqlite3_close(handle) }; throw Failure.database
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 5000)
        try run(db, "PRAGMA journal_mode=WAL")
        for sql in [
            "CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)",
            "CREATE INDEX IF NOT EXISTS stories_updated ON stories(updated)",
            "CREATE TABLE IF NOT EXISTS members(url TEXT PRIMARY KEY, story_id TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT NOT NULL, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS consumers(name TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0)"
        ] { try run(db, sql) }
        try run(db, "BEGIN IMMEDIATE")
        do {
            let value = try work(db)
            try run(db, "COMMIT")
            return value
        } catch { _ = try? run(db, "ROLLBACK"); throw error }
    }

    @discardableResult private func run(_ db: OpaquePointer, _ sql: String, _ values: [String] = []) throws -> [[String]] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { throw Failure.database }
        defer { sqlite3_finalize(stmt) }
        for (i, value) in values.enumerated() {
            sqlite3_bind_text(stmt, Int32(i+1), value, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        }
        var rows: [[String]] = []
        while true {
            let status = sqlite3_step(stmt)
            if status == SQLITE_DONE { return rows }
            guard status == SQLITE_ROW else { throw Failure.database }
            rows.append((0..<sqlite3_column_count(stmt)).map { sqlite3_column_text(stmt, $0).map { String(cString: $0) } ?? "" })
        }
    }
    private func encode(_ value: Any) throws -> String {
        String(decoding: try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes]), as: UTF8.self)
    }
    private func decode(_ text: String) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { throw Failure.database }
        return value
    }
    private func contentKey(_ sources: [[String: Any]]) -> [[String]] {
        sources.map { [$0["url"] as? String ?? "", $0["title"] as? String ?? "", $0["summary"] as? String ?? ""] }
    }

    func ingest(_ articles: [Article]) throws -> [Article] {
        var unique: [String: Article] = [:]
        for article in articles {
            let provider = (article.wire["extra"] as? [String: Any])?["provider"] as? String ?? "direct"
            let key = provider + "\n" + CanonicalURL.fingerprint(article.url)
            if let old = unique[key] {
                let a = [article.title, article.summary, article.source, article.desk.rawValue]
                let b = [old.title, old.summary, old.source, old.desk.rawValue]
                if article.summary.count < old.summary.count ||
                    (article.summary.count == old.summary.count && !b.lexicographicallyPrecedes(a)) { continue }
            }
            unique[key] = article
        }
        let candidates = unique.keys.sorted().compactMap { unique[$0] }
        return try withDB { db in
            var changed: [String: Article] = [:]
            let now = Date()
            let stamp = ISO8601DateFormatter().string(from: now)
            for article in candidates {
                let input = article.wire
                let url = CanonicalURL.fingerprint(article.url)
                let row = try run(db, "SELECT s.payload FROM members m JOIN stories s ON s.id=m.story_id WHERE m.url=?", [url]).first
                var old = try row.map { try decode($0[0]) }
                let special = ["weather", "markets", "polymarket"]
                if old == nil && !special.contains(article.desk.rawValue) {
                    let tokens = FreshnessStore.titleTokens(article.title)
                    for row in try run(db, "SELECT payload FROM stories WHERE updated>=? ORDER BY updated DESC LIMIT 500", [String(now.timeIntervalSince1970 - 48*3600)]) {
                        let candidate = try decode(row[0])
                        if special.contains(candidate["desk"] as? String ?? "") { continue }
                        if FreshnessStore.isNearDuplicateTitle(tokens, FreshnessStore.titleTokens(candidate["title"] as? String ?? "")) {
                            old = candidate; break
                        }
                    }
                }
                let extra = input["extra"] as? [String: Any] ?? [:]
                var source: [String: Any] = ["url": url, "title": article.title, "source": article.source,
                    "published": input["published"] ?? "", "summary": article.summary,
                    "providers": [extra["provider"] as? String ?? "direct"], "metadata": extra["metadata"] ?? [:]]
                let previous = old?["sources"] as? [[String: Any]] ?? []
                var sources: [String: [String: Any]] = [:]
                for s in previous { if let u = s["url"] as? String { sources[u] = s } }
                if let prior = sources[url] {
                    let providers = prior["providers"] as? [String] ?? []
                    source["providers"] = Array(Set(providers + (source["providers"] as? [String] ?? []))).sorted()
                    if (source["metadata"] as? [String: Any] ?? [:]).isEmpty { source["metadata"] = prior["metadata"] }
                    if extra["provider"] as? String == "worldmonitor" && providers.contains("direct") {
                        source["summary"] = prior["summary"]; source["title"] = prior["title"]
                    }
                }
                sources[url] = source
                let ordered = sources.keys.sorted().compactMap { sources[$0] }
                let substantive = old == nil || contentKey(ordered) != contentKey(previous)
                var story = old ?? input
                story.removeValue(forKey: "extra")
                let id = old?["id"] as? String ?? article.id
                story["id"] = id; story["story_id"] = id; story["sources"] = ordered
                let digestOverDirect = extra["provider"] as? String == "worldmonitor" &&
                    previous.contains { $0["url"] as? String == url && ($0["providers"] as? [String] ?? []).contains("direct") }
                if url == story["url"] as? String && !digestOverDirect {
                    for k in ["title", "summary", "tweet", "tweet_chars", "hashtags", "published"] { story[k] = input[k] }
                }
                story["first_seen"] = old?["first_seen"] ?? stamp
                if substantive {
                    story["revision"] = (old?["revision"] as? Int ?? 0) + 1
                    story["change"] = old == nil ? "new" : "updated"
                    story["updated_at"] = stamp
                }
                try run(db, "INSERT OR REPLACE INTO stories VALUES (?,?,?)", [id, try encode(story), String(now.timeIntervalSince1970)])
                try run(db, "INSERT OR REPLACE INTO members VALUES (?,?)", [url, id])
                if substantive {
                    try run(db, "INSERT INTO events(story_id,payload) VALUES (?,?)", [id, try encode(story)])
                    story["event_seq"] = Int(sqlite3_last_insert_rowid(db))
                    changed[id] = try Article.fromWire(story)
                }
            }
            return Array(changed.values)
        }
    }
    private func validate(_ consumer: String) throws {
        guard consumer.range(of: "^[A-Za-z0-9_.-]{1,64}$", options: .regularExpression) == consumer.startIndex..<consumer.endIndex else { throw Failure.invalidConsumer }
    }
    func pending(consumer: String, limit: Int = 100) throws -> [String: Any] {
        try validate(consumer)
        guard (1...500).contains(limit) else { throw Failure.invalidLimit }
        return try withDB { db in
            try run(db, "INSERT OR IGNORE INTO consumers(name) VALUES (?)", [consumer])
            let row = try run(db, "SELECT cursor,offered FROM consumers WHERE name=?", [consumer])[0]
            let cursor = Int(row[0]) ?? 0, offered = Int(row[1]) ?? 0
            let rows = try run(db, "SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", [String(cursor), String(limit)])
            let end = rows.last.flatMap { Int($0[0]) } ?? cursor
            try run(db, "UPDATE consumers SET offered=? WHERE name=?", [String(max(offered, end)), consumer])
            let events: [[String: Any]] = try rows.map { ["seq": Int($0[0]) ?? 0, "story": try decode($0[1])] }
            return ["consumer": consumer, "cursor": cursor, "next_cursor": end, "count": events.count, "events": events]
        }
    }
    func acknowledge(consumer: String, through: Int) throws -> [String: Any] {
        try validate(consumer)
        return try withDB { db in
            guard let row = try run(db, "SELECT cursor,offered FROM consumers WHERE name=?", [consumer]).first,
                  through >= 0, through <= (Int(row[1]) ?? 0) else { throw Failure.invalidAcknowledgement }
            let cursor = max(Int(row[0]) ?? 0, through)
            try run(db, "UPDATE consumers SET cursor=? WHERE name=?", [String(cursor), consumer])
            return ["consumer": consumer, "cursor": cursor]
        }
    }
}
