import Foundation
import CoreFoundation
import SQLite3

/// A bound on automated ingestion, not a quota on manual work, backups or WAL.
enum StoragePolicy {
    static let defaults = ["version": 0, "max_journal_bytes": 2 * 1024 * 1024 * 1024, "min_free_bytes": 256 * 1024 * 1024]
    static func validate(_ value: [String: Any]) throws -> [String: Int] {
        guard Set(value.keys) == Set(defaults.keys) else { throw LibraryFailure.invalid("invalid ingestion storage policy") }
        var result: [String: Int] = [:]
        for (key, raw) in value {
            guard let n = raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
                  !["d", "f"].contains(String(cString: n.objCType)), n.doubleValue == Double(n.intValue) else {
                throw LibraryFailure.invalid("storage limits must be integers")
            }
            result[key] = n.intValue
        }
        guard (0...Int.max).contains(result["version"]!),
              (1024*1024...8*1024*1024*1024).contains(result["max_journal_bytes"]!),
              (64*1024*1024...8*1024*1024*1024).contains(result["min_free_bytes"]!) else {
            throw LibraryFailure.invalid("storage limits outside supported bounds")
        }
        return result
    }
    static func fileSize(_ url: URL) throws -> Int {
        let attrs: [FileAttributeKey: Any]
        do { attrs = try FileManager.default.attributesOfItem(atPath: url.path) }
        catch CocoaError.fileReadNoSuchFile { return 0 }
        guard attrs[.type] as? FileAttributeType == .typeRegular else { throw StoryStore.Failure.database }
        return (attrs[.size] as? NSNumber)?.intValue ?? 0
    }
}

enum StorageFailure: Error { case blocked }

extension StoryStore {
    func storagePolicy(_ db: OpaquePointer) throws -> [String: Int] {
        if let row = try run(db, "SELECT payload FROM library_preferences WHERE kind='storage' AND name='ingestion-policy'").first {
            return try StoragePolicy.validate(decode(row[0]))
        }
        return StoragePolicy.defaults
    }
    func storageSnapshot(_ db: OpaquePointer) throws -> [String: Any] {
        func scalar(_ sql: String) throws -> Int {
            guard let raw = try run(db, sql).first?.first, let n = Int(raw) else { throw Failure.database }
            return n
        }
        let policy = try storagePolicy(db), pageSize = try scalar("PRAGMA page_size")
        let pages = try scalar("PRAGMA page_count"), reusable = try scalar("PRAGMA freelist_count")
        let attrs = try FileManager.default.attributesOfFileSystem(forPath: path.deletingLastPathComponent().path)
        guard let free = (attrs[.systemFreeSize] as? NSNumber)?.intValue else { throw Failure.database }
        let wal = try StoragePolicy.fileSize(URL(fileURLWithPath: path.path + "-wal"))
        var reasons: [String] = []
        if pages * pageSize > policy["max_journal_bytes"]! { reasons.append("journal_above_ceiling") }
        if free < policy["min_free_bytes"]! { reasons.append("disk_below_reserve") }
        if wal > policy["max_journal_bytes"]! { reasons.append("wal_above_ceiling") }
        return ["policy": policy, "database_bytes": pages * pageSize, "database_file_bytes": try StoragePolicy.fileSize(path),
                "reusable_bytes": reusable * pageSize, "wal_bytes": wal, "free_bytes": free,
                "stories": try scalar("SELECT count(*) FROM stories"), "events": try scalar("SELECT count(*) FROM events"),
                "unread_alerts": try scalar("SELECT count(*) FROM watch_matches WHERE seen_seq<seq"),
                "bookmarked_stories": try scalar("SELECT count(*) FROM editorial WHERE json_extract(payload,'$.bookmarked')=1"),
                "ingestion_blocked": !reasons.isEmpty, "blocking_reasons": reasons,
                "nearing_ceiling": Double((pages-reusable)*pageSize) >= Double(policy["max_journal_bytes"]!) * 0.9,
                "automatic_pruning": false, "scope": "journal ingestion, not a filesystem quota"]
    }
    func storageGuard(_ db: OpaquePointer) throws {
        let value = try storageSnapshot(db)
        guard value["ingestion_blocked"] as? Bool == false else { throw StorageFailure.blocked }
        let policy = value["policy"] as! [String: Int]
        guard let size = Int(try run(db, "PRAGMA page_size")[0][0]) else { throw Failure.database }
        let expected = policy["max_journal_bytes"]! / size
        guard try run(db, "PRAGMA max_page_count=\(expected)").first?.first == String(expected) else { throw StorageFailure.blocked }
    }
    func storagePreflight() throws { try withDB { try storageGuard($0) } }
    func storageStatus() throws -> [String: Any] { try withDB { try storageSnapshot($0) } }
    func storageChange(_ value: [String: Any]) throws -> [String: Any] {
        guard Set(value.keys) == ["expected_version", "max_journal_bytes", "min_free_bytes"] else {
            throw LibraryFailure.invalid("expected_version and both storage limits required")
        }
        var input = value; input["version"] = input.removeValue(forKey: "expected_version")
        var policy = try StoragePolicy.validate(input)
        return try withDB { db in
            guard try storagePolicy(db)["version"] == policy["version"] else {
                throw LibraryFailure.conflict("storage policy changed; reload before saving")
            }
            guard policy["version"]! < Int.max else { throw LibraryFailure.invalid("storage policy version exhausted") }
            policy["version"]! += 1
            try run(db, "INSERT OR REPLACE INTO library_preferences VALUES ('storage','ingestion-policy',?)", [try encode(policy)])
            return policy
        }
    }
}

enum OperatorReport {
    static func read(stories: StoryStore?, health: SourceHealth) -> [String: Any] {
        var sections: [String: Any] = [:], errors: [String: String] = [:]
        let reads: [(String, () throws -> Any)] = [
            ("storage", { guard let stories else { throw StoryStore.Failure.database }; return try stories.storageStatus() }),
            ("delivery", { guard let stories else { throw StoryStore.Failure.database }; return try stories.consumerStatus() }),
            ("sources", { try health.list() })]
        for (name, read) in reads {
            do { sections[name] = try read() }
            catch { errors[name] = "Local storage unavailable; inspect the selected application home." }
        }
        return ["observed_at": ISO8601DateFormatter().string(from: Date()), "sections": sections, "errors": errors,
                "complete": errors.isEmpty, "consistency": "separate local snapshots; reload after changes"]
    }
}
