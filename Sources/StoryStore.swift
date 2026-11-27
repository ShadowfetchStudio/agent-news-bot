import Foundation
import SQLite3

/// Durable revisions. Delivery is at-least-once, with a separate acknowledgement per consumer.
final class StoryStore: @unchecked Sendable {
    private let path: URL
    private let lock = NSLock()
    init(path: URL) { self.path = path }
    enum Failure: Error { case database, invalidConsumer, invalidLimit, invalidAcknowledgement }

    func withDB<T>(_ work: (OpaquePointer) throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        do { try AppPaths.prepareDatabase(path) } catch { throw Failure.database }
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
            "CREATE TABLE IF NOT EXISTS consumers(name TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS events_story_seq ON events(story_id,seq)",
            "CREATE TABLE IF NOT EXISTS identity_aliases(alias TEXT PRIMARY KEY, story_id TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY)",
            "CREATE TABLE IF NOT EXISTS editorial(story_id TEXT PRIMARY KEY,payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS library_preferences(kind TEXT NOT NULL,name TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,name))"
        ] { try run(db, sql) }
        try run(db, ConsumerSchema.sql)
        try run(db, "BEGIN IMMEDIATE")
        do {
            if try run(db, "SELECT 1 FROM migrations WHERE name='canonical-v2'").isEmpty {
                for row in try run(db, "SELECT url,story_id FROM members ORDER BY url") {
                    let normalized = CanonicalURL.fingerprint(row[0])
                    try run(db, "INSERT OR IGNORE INTO members VALUES (?,?)", [normalized, row[1]])
                    try run(db, "INSERT OR IGNORE INTO identity_aliases VALUES (?,?)", [FreshnessStore.articleID(url: normalized, fallback: ""), row[1]])
                }
                try run(db, "INSERT INTO migrations VALUES ('canonical-v2')")
            }
            let value = try work(db)
            try run(db, "COMMIT")
            return value
        } catch { _ = try? run(db, "ROLLBACK"); throw error }
    }

    @discardableResult func run(_ db: OpaquePointer, _ sql: String, _ values: [String] = []) throws -> [[String]] {
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
    func encode(_ value: Any) throws -> String {
        String(decoding: try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes]), as: UTF8.self)
    }
    func decode(_ text: String) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { throw Failure.database }
        return value
    }
    private let contextFields = ["location", "locationName", "threat", "tickers", "linkVerified", "credibilityUnassessed"]

    private func editorialChanged(_ sources: [[String: Any]], _ previous: [[String: Any]]) throws -> Bool {
        let selected: ([[String: Any]]) -> [[String]] = { rows in rows.map { row in ["url", "title", "summary"].map { row[$0] as? String ?? "" } } }
        if selected(sources) != selected(previous) { return true }
        for source in sources {
            let prior = previous.first { $0["url"] as? String == source["url"] as? String }
            let before = prior?["observations"] as? [String: [String: Any]] ?? [:]
            let after = source["observations"] as? [String: [String: Any]] ?? [:]
            for provider in Set(before.keys).intersection(after.keys) {
                if ["title", "summary"].contains(where: { before[provider]?[$0] as? String != after[provider]?[$0] as? String }) { return true }
            }
        }
        return false
    }
    private func contextKey(_ story: [String: Any]) throws -> String {
        let sources = story["sources"] as? [[String: Any]] ?? []
        return try encode(["desks": story["desks"] ?? [story["desk"] ?? ""], "sources": sources.map { source -> [String: Any] in
            let metadata = source["metadata"] as? [String: Any] ?? [:]
            return ["url": source["url"] ?? "", "providers": source["providers"] ?? [],
                "context": Dictionary(uniqueKeysWithValues: contextFields.map { ($0, metadata[$0] ?? NSNull()) })]
        }])
    }
    private func disagree(_ sources: [[String: Any]]) -> Bool {
        let negative: Set<String> = ["not", "never", "no", "cancel", "cancels", "cancelled", "canceled", "denies", "rejected"]
        for source in sources {
            let observations = source["observations"] as? [String: [String: Any]] ?? [:]
            let titles = Set(observations.values.map { ($0["title"] as? String ?? "").lowercased().trimmingCharacters(in: .whitespacesAndNewlines) })
            if titles.count > 1 { return true }
        }
        let titles = sources.map { $0["title"] as? String ?? "" }
        for i in titles.indices {
            for j in titles.indices where j > i {
                let a = Set(titles[i].lowercased().split(separator: " ").map(String.init))
                let b = Set(titles[j].lowercased().split(separator: " ").map(String.init))
                if a.isDisjoint(with: negative) != b.isDisjoint(with: negative) &&
                    FreshnessStore.isNearDuplicateTitle(FreshnessStore.titleTokens(titles[i]), FreshnessStore.titleTokens(titles[j]), threshold: 0.5) { return true }
            }
        }
        return false
    }

    func ingest(_ articles: [Article]) throws -> [Article] {
        guard articles.allSatisfy({ ArticleURL.validated($0.url) != nil }) else { throw URLError(.unsupportedURL) }
        var unique: [String: Article] = [:]
        var desksByURL: [String: Set<String>] = [:]
        for article in articles {
            let url = CanonicalURL.fingerprint(article.url)
            desksByURL[url, default: []].insert(article.desk.rawValue)
            let provider = (article.wire["extra"] as? [String: Any])?["provider"] as? String ?? "direct"
            let key = provider + "\n" + url
            if let old = unique[key] {
                let a = [article.title, article.summary, article.source, article.desk.rawValue]
                let b = [old.title, old.summary, old.source, old.desk.rawValue]
                if article.summary.count < old.summary.count ||
                    (article.summary.count == old.summary.count && !b.lexicographicallyPrecedes(a)) { continue }
            }
            unique[key] = article
        }
        return try withDB { db in
            var baseline: [String: [String: Any]] = [:]
            var current: [String: [String: Any]] = [:]
            var touched = Set<String>()
            let now = Date(), stamp = ISO8601DateFormatter().string(from: Date())
            for key in unique.keys.sorted() {
                let article = unique[key]!, input = article.wire
                let url = CanonicalURL.fingerprint(article.url)
                let row = try run(db, "SELECT s.payload FROM members m JOIN stories s ON s.id=m.story_id WHERE m.url=?", [url]).first
                var old = try row.map { try decode($0[0]) }
                let special = ["weather", "markets", "polymarket"]
                if old == nil && !special.contains(article.desk.rawValue) {
                    for row in try run(db, "SELECT payload FROM stories WHERE updated>=? ORDER BY updated DESC LIMIT 500", [String(now.timeIntervalSince1970 - 48*3600)]) {
                        let candidate = try decode(row[0])
                        if special.contains(candidate["desk"] as? String ?? "") { continue }
                        if FreshnessStore.canGroupTitles(article.title, candidate["title"] as? String ?? "") { old = candidate; break }
                    }
                }
                let id = old?["id"] as? String ?? article.id
                if !touched.contains(id) { baseline[id] = old; touched.insert(id) }
                let original = baseline[id]
                let version = (original?["observation_version"] as? Int ?? original?["revision"] as? Int ?? 0) + 1
                var sources: [String: [String: Any]] = [:]
                for var source in old?["sources"] as? [[String: Any]] ?? [] {
                    if let u = source["url"] as? String {
                        let normalized = CanonicalURL.fingerprint(u)
                        source["url"] = normalized; sources[normalized] = source
                    }
                }
                let prior = sources[url] ?? [:]
                var observations = prior["observations"] as? [String: [String: Any]] ?? [:]
                if observations.isEmpty && !prior.isEmpty {
                    for name in prior["providers"] as? [String] ?? ["direct"] {
                        var imported = Dictionary(uniqueKeysWithValues: ["title", "summary", "source", "published"].map { ($0, prior[$0] ?? "") })
                        imported["changed_version"] = 0; imported["legacy_imported"] = true
                        observations[name] = imported
                    }
                }
                let extra = input["extra"] as? [String: Any] ?? [:]
                let provider = extra["provider"] as? String ?? "direct"
                var observation = Dictionary(uniqueKeysWithValues: ["title", "summary", "source", "published"].map { ($0, input[$0] ?? "") })
                let before = observations[provider]
                let editorialChange = before == nil || ["title", "summary"].contains { before?[$0] as? String != observation[$0] as? String }
                observation["metadata"] = extra["metadata"] ?? [:]
                observation["last_seen"] = stamp
                observation["changed_at"] = editorialChange ? stamp : before?["changed_at"] ?? stamp
                observation["changed_version"] = editorialChange ? version : before?["changed_version"] ?? 0
                observations[provider] = observation
                let winner = observations.keys.sorted { a, b in
                    let av = observations[a]?["changed_version"] as? Int ?? 0, bv = observations[b]?["changed_version"] as? Int ?? 0
                    if av != bv { return av > bv }
                    if (a == "direct") != (b == "direct") { return a == "direct" }
                    return a > b
                }.first!
                let chosen = observations[winner]!
                let summary = observations.keys.sorted().compactMap { name -> String? in
                    let o = observations[name]!
                    return o["title"] as? String == chosen["title"] as? String ? o["summary"] as? String : nil
                }.max { $0.count < $1.count } ?? ""
                let wmMetadata = observations["worldmonitor"]?["metadata"] as? [String: Any] ?? [:]
                let ownMetadata = chosen["metadata"] as? [String: Any] ?? [:]
                var source = chosen
                source["url"] = url; source["summary"] = summary
                source["providers"] = observations.keys.sorted(); source["selected_provider"] = winner
                source["observations"] = observations
                source["metadata"] = !wmMetadata.isEmpty ? wmMetadata : !ownMetadata.isEmpty ? ownMetadata : prior["metadata"] ?? [:]
                sources[url] = source
                var story = old ?? input
                story.removeValue(forKey: "extra")
                story["id"] = id; story["story_id"] = id
                story["url"] = CanonicalURL.fingerprint(story["url"] as? String ?? url)
                story["grouping_policy"] = story["grouping_policy"] ?? (old == nil ? "equivalent-wording-v1" : "legacy-title-overlap")
                story["sources"] = sources.keys.sorted().compactMap { sources[$0] }
                story["observation_version"] = version
                story["desks"] = Set(story["desks"] as? [String] ?? [story["desk"] as? String ?? article.desk.rawValue]).union(desksByURL[url] ?? []).sorted()
                let main = sources[story["url"] as? String ?? ""] ?? source
                for field in ["title", "summary", "source", "published"] { story[field] = main[field] ?? story[field] }
                let (tweet, chars) = Hashtagger.tweet(title: story["title"] as? String ?? "", url: story["url"] as? String ?? url, tags: story["hashtags"] as? [String] ?? [])
                story["tweet"] = tweet; story["tweet_chars"] = chars
                story["first_seen"] = old?["first_seen"] ?? extra["observed_at"] ?? stamp
                let disagreement = disagree(story["sources"] as? [[String: Any]] ?? [])
                story["disagreement"] = disagreement
                story["review_note"] = disagreement ? "Reports differ; review source observations before use." : ""
                try run(db, "INSERT OR REPLACE INTO stories VALUES (?,?,?)", [id, try encode(story), String(now.timeIntervalSince1970)])
                try run(db, "INSERT OR REPLACE INTO members VALUES (?,?)", [url, id])
                try run(db, "INSERT OR IGNORE INTO identity_aliases VALUES (?,?)", [article.id, id])
                current[id] = story
            }
            var changed: [Article] = []
            for id in current.keys.sorted() {
                var story = current[id]!
                let original = baseline[id]
                let editorial = try original == nil || editorialChanged(story["sources"] as? [[String: Any]] ?? [], original?["sources"] as? [[String: Any]] ?? [])
                let enrichment = try original != nil && contextKey(story) != contextKey(original!)
                if editorial || enrichment {
                    story["revision"] = (original?["revision"] as? Int ?? 0) + 1
                    story["change"] = original == nil ? "new" : editorial ? "updated" : "enriched"
                    story["updated_at"] = stamp
                    story["changed_fields"] = try ["title", "summary", "sources", "desks", "disagreement"].filter { try encode([original?[$0] ?? NSNull()]) != encode([story[$0] ?? NSNull()]) }
                    try run(db, "INSERT INTO events(story_id,payload) VALUES (?,?)", [id, try encode(story)])
                    story["event_seq"] = Int(sqlite3_last_insert_rowid(db))
                    changed.append(try Article.fromWire(story))
                }
                try run(db, "INSERT OR REPLACE INTO stories VALUES (?,?,?)", [id, try encode(story), String(now.timeIntervalSince1970)])
            }
            return changed
        }
    }

    func story(_ id: String) throws -> [String: Any]? {
        try withDB { db in try run(db, "SELECT payload FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", [id, id]).first.map { try decode($0[0]) } }
    }
    func revisions(_ id: String, limit: Int = 50) throws -> [[String: Any]] {
        guard (1...100).contains(limit) else { throw Failure.invalidLimit }
        return try withDB { db in
            let rows = try run(db, "SELECT seq,payload FROM events WHERE story_id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?)) ORDER BY seq DESC LIMIT ?", [id, id, String(limit + 1)]).reversed()
            var previous: [String: Any] = [:], result: [[String: Any]] = []
            for row in rows {
                let story = try decode(row[1])
                var diff: [String: Any] = [:]
                for field in ["title", "summary", "sources", "desks", "disagreement"] {
                    if try encode([previous[field] ?? NSNull()]) != encode([story[field] ?? NSNull()]) {
                        diff[field] = ["before": previous[field] ?? NSNull(), "after": story[field] ?? NSNull()]
                    }
                }
                result.append(["seq": Int(row[0]) ?? 0, "story": story, "diff": diff])
                previous = story
            }
            return Array(result.suffix(limit))
        }
    }
    func validate(_ consumer: String) throws {
        guard consumer.range(of: "^[A-Za-z0-9_.-]{1,64}$", options: .regularExpression) == consumer.startIndex..<consumer.endIndex else { throw Failure.invalidConsumer }
    }
    func pending(consumer: String, limit: Int = 100, lease: String? = nil) throws -> [String: Any] {
        try validate(consumer)
        guard (1...500).contains(limit) else { throw Failure.invalidLimit }
        return try withDB { db in
            try run(db, "INSERT OR IGNORE INTO consumers(name) VALUES (?)", [consumer])
            let generation = try deliveryGuard(db, consumer: consumer, lease: lease)
            let row = try run(db, "SELECT cursor,offered FROM consumers WHERE name=?", [consumer])[0]
            let cursor = Int(row[0]) ?? 0, offered = Int(row[1]) ?? 0
            let rows = try run(db, "SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", [String(cursor), String(limit)])
            let end = rows.last.flatMap { Int($0[0]) } ?? cursor
            try run(db, "UPDATE consumers SET offered=? WHERE name=?", [String(max(offered, end)), consumer])
            let events: [[String: Any]] = try rows.map { ["seq": Int($0[0]) ?? 0, "story": try decode($0[1])] }
            return ["consumer": consumer, "generation": generation, "cursor": cursor, "next_cursor": end, "count": events.count, "events": events]
        }
    }
    func acknowledge(consumer: String, through: Int, generation: Int? = nil, lease: String? = nil) throws -> [String: Any] {
        try validate(consumer)
        return try withDB { db in
            let generation = try deliveryGuard(db, consumer: consumer, generation: generation, lease: lease, ack: true)
            guard let row = try run(db, "SELECT cursor,offered FROM consumers WHERE name=?", [consumer]).first,
                  through >= 0, through <= (Int(row[1]) ?? 0) else { throw Failure.invalidAcknowledgement }
            let cursor = max(Int(row[0]) ?? 0, through)
            try run(db, "UPDATE consumers SET cursor=? WHERE name=?", [String(cursor), consumer])
            try run(db, "UPDATE consumer_meta SET last_ack=? WHERE name=?", [String(Date().timeIntervalSince1970), consumer])
            return ["consumer": consumer, "cursor": cursor, "generation": generation]
        }
    }
}
