import Foundation
import SQLite3
import CryptoKit

final class FreshnessStore: @unchecked Sendable {
    private let lock = NSLock()
    private var db: OpaquePointer?

    init(path: URL) {
        do { try AppPaths.prepareDatabase(path) } catch { return }
        if sqlite3_open(path.path, &db) != SQLITE_OK {
            db = nil
            return
        }
        sqlite3_busy_timeout(db, 5000)
        sqlite3_exec(db, "PRAGMA journal_mode=WAL;", nil, nil, nil)
        sqlite3_exec(
            db,
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
            CREATE TABLE IF NOT EXISTS feed_documents (url TEXT PRIMARY KEY, body TEXT NOT NULL, observed_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS feed_cache (
                url TEXT PRIMARY KEY,
                etag TEXT,
                last_modified TEXT,
                updated_at REAL
            );
            """,
            nil,
            nil,
            nil
        )
    }

    deinit {
        if db != nil { sqlite3_close(db) }
    }

    func hasSeen(_ id: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard db != nil else { return false }
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT 1 FROM seen WHERE id = ? LIMIT 1", -1, &stmt, nil) == SQLITE_OK else {
            return false
        }
        bind(stmt, 1, id)
        return sqlite3_step(stmt) == SQLITE_ROW
    }

    func mark(id: String, url: String, title: String, desk: String) {
        lock.lock()
        defer { lock.unlock() }
        exec(
            "INSERT OR REPLACE INTO seen(id, url, title, desk, seen_at) VALUES (?,?,?,?,?)",
            bind: { stmt in
                self.bind(stmt, 1, id)
                self.bind(stmt, 2, url)
                self.bind(stmt, 3, title)
                self.bind(stmt, 4, desk)
                sqlite3_bind_double(stmt, 5, Date().timeIntervalSince1970)
            }
        )
    }

    func pruneSeen(olderThanDays days: Double = 30) {
        lock.lock()
        defer { lock.unlock() }
        let cutoff = Date().timeIntervalSince1970 - days * 86_400
        exec(
            "DELETE FROM seen WHERE seen_at < ?",
            bind: { stmt in
                sqlite3_bind_double(stmt, 1, cutoff)
            }
        )
    }

    func lastQuote(_ symbol: String) -> String? {
        lock.lock()
        defer { lock.unlock() }
        guard db != nil else { return nil }
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT payload FROM quotes WHERE symbol = ?", -1, &stmt, nil) == SQLITE_OK else {
            return nil
        }
        bind(stmt, 1, symbol)
        guard sqlite3_step(stmt) == SQLITE_ROW, let c = sqlite3_column_text(stmt, 0) else { return nil }
        return String(cString: c)
    }

    func saveQuote(_ symbol: String, payload: String) {
        lock.lock()
        defer { lock.unlock() }
        exec(
            "INSERT OR REPLACE INTO quotes(symbol, payload, updated_at) VALUES (?,?,?)",
            bind: { stmt in
                self.bind(stmt, 1, symbol)
                self.bind(stmt, 2, payload)
                sqlite3_bind_double(stmt, 3, Date().timeIntervalSince1970)
            }
        )
    }

    func feedCache(_ url: String) -> (etag: String?, lastModified: String?) {
        lock.lock()
        defer { lock.unlock() }
        guard db != nil else { return (nil, nil) }
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT etag, last_modified FROM feed_cache WHERE url = ?", -1, &stmt, nil) == SQLITE_OK else {
            return (nil, nil)
        }
        bind(stmt, 1, url)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return (nil, nil) }
        let etag = sqlite3_column_text(stmt, 0).map { String(cString: $0) }
        let lm = sqlite3_column_text(stmt, 1).map { String(cString: $0) }
        return (etag, lm)
    }

    func feedDocument(_ url: String) -> (data: Data, observedAt: Date)? {
        lock.lock(); defer { lock.unlock() }
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT body,observed_at FROM feed_documents WHERE url=?", -1, &stmt, nil) == SQLITE_OK else { return nil }
        bind(stmt, 1, url)
        guard sqlite3_step(stmt) == SQLITE_ROW, let raw = sqlite3_column_text(stmt, 0),
            let data = Data(base64Encoded: String(cString: raw)) else { return nil }
        return (data, Date(timeIntervalSince1970: sqlite3_column_double(stmt, 1)))
    }

    func saveFeedCache(_ url: String, etag: String?, lastModified: String?, body: Data? = nil, observedAt: Date? = nil) {
        guard body == nil || body!.count <= 4 * 1024 * 1024 else { return }
        lock.lock(); defer { lock.unlock() }
        guard exec("BEGIN IMMEDIATE") else { return }
        let markerSaved = exec("INSERT OR REPLACE INTO feed_cache(url,etag,last_modified,updated_at) VALUES (?,?,?,?)") { stmt in
            self.bind(stmt, 1, url); self.bind(stmt, 2, etag ?? ""); self.bind(stmt, 3, lastModified ?? "")
            sqlite3_bind_double(stmt, 4, Date().timeIntervalSince1970)
        }
        let bodySaved: Bool
        if let body {
            bodySaved = exec("INSERT OR REPLACE INTO feed_documents VALUES (?,?,?)") { stmt in
                self.bind(stmt, 1, url); self.bind(stmt, 2, body.base64EncodedString())
                sqlite3_bind_double(stmt, 3, (observedAt ?? Date()).timeIntervalSince1970)
            }
        } else {
            bodySaved = exec("DELETE FROM feed_documents WHERE url=?") { self.bind($0, 1, url) }
        }
        if markerSaved && bodySaved {
            if !exec("COMMIT") { exec("ROLLBACK") }
        } else { exec("ROLLBACK") }
    }

    func isWithinLookback(_ published: Date?, hours: Double) -> Bool {
        guard let published else { return true }
        return published >= Date().addingTimeInterval(-hours * 3600) && published <= Date().addingTimeInterval(300)
    }

    static func articleID(url: String, fallback: String) -> String {
        let canon = CanonicalURL.fingerprint(url)
        let digest = SHA256.hash(data: Data(canon.utf8))
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return hex.isEmpty ? fallback : String(hex.prefix(24))
    }

    static func titleTokens(_ title: String) -> Set<String> {
        let stop: Set<String> = [
            "the", "and", "for", "that", "with", "from", "this", "have", "will",
            "are", "was", "were", "has", "had", "into", "over", "after", "says", "said"
        ]
        let words = title.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { $0.count >= 3 && !stop.contains($0) }
        return Set(words)
    }

    static func isNearDuplicateTitle(_ a: Set<String>, _ b: Set<String>, threshold: Double = 0.72) -> Bool {
        guard a.count >= 4, b.count >= 4 else { return false }
        let inter = a.intersection(b).count
        let union = a.union(b).count
        return union > 0 && (Double(inter) / Double(union)) >= threshold
    }

    private func bind(_ stmt: OpaquePointer?, _ idx: Int32, _ value: String) {
        sqlite3_bind_text(stmt, idx, value, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
    }

    @discardableResult private func exec(_ sql: String, bind: ((OpaquePointer?) -> Void)? = nil) -> Bool {
        guard db != nil else { return false }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return false }
        defer { sqlite3_finalize(stmt) }
        bind?(stmt)
        return sqlite3_step(stmt) == SQLITE_DONE
    }
}
