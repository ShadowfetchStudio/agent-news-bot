import Foundation
import CoreFoundation
import SQLite3

enum AlertSchema {
    static let sql = [
        "CREATE TABLE IF NOT EXISTS watch_clock(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL CHECK(typeof(value)='integer' AND value>=0))",
        "CREATE TABLE IF NOT EXISTS watch_alerts(name TEXT PRIMARY KEY,payload TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS watch_matches(alert TEXT NOT NULL,story_id TEXT NOT NULL,seq INTEGER NOT NULL,revision INTEGER NOT NULL,subscription_version INTEGER NOT NULL,seen_seq INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(alert,story_id))"
    ]
    static let filterKeys: Set<String> = ["q","entity","desk","source","provider","status","bookmarked","include_muted"]
    static func query(_ value: [String: String]) throws -> (String, [String]) {
        guard Set(value.keys).isSubset(of: filterKeys) else { throw LibraryFailure.invalid("alert filters cannot include pagination or sort") }
        let request = try LibraryModel.query(value)
        guard let range = request.sql.range(of: " ORDER BY ", options: .backwards) else { throw StoryStore.Failure.database }
        return (String(request.sql[..<range.lowerBound]) + " AND s.id=? LIMIT 1", Array(request.values.dropLast(2)))
    }
}

extension StoryStore {
    func recordAlerts(_ db: OpaquePointer, storyIDs: [String]) throws {
        for row in try run(db, "SELECT name,payload FROM watch_alerts ORDER BY name") {
            let subscription = try decode(row[1])
            guard subscription["enabled"] as? Bool == true else { continue }
            guard let filters = subscription["filters"] as? [String: String], let version = subscription["version"] as? Int,
                  let start = subscription["start_seq"] as? Int else { throw Failure.database }
            let (sql, args) = try AlertSchema.query(filters)
            for id in storyIDs {
                guard let match = try run(db, sql, args + [id]).first else { continue }
                let story = try decode(match[0])
                guard let seq = story["event_seq"] as? Int, let revision = story["revision"] as? Int else { throw Failure.database }
                if seq <= start { continue }
                try run(db, """
                    INSERT INTO watch_matches(alert,story_id,seq,revision,subscription_version) VALUES (?,?,?,?,?)
                    ON CONFLICT(alert,story_id) DO UPDATE SET seq=excluded.seq,revision=excluded.revision,
                    subscription_version=excluded.subscription_version WHERE excluded.seq>watch_matches.seq
                    """, [row[0],id,String(seq),String(revision),String(version)])
            }
        }
    }
    func alerts(_ query: [String: String] = [:]) throws -> [String: Any] {
        guard Set(query.keys).isSubset(of: ["name","unread","limit","offset"]),
              let limit = Int(query["limit"] ?? "50"), (1...100).contains(limit),
              let offset = Int(query["offset"] ?? "0"), (0...100000).contains(offset),
              ["true","false"].contains(query["unread"] ?? "true"), (query["name"] ?? "").unicodeScalars.count <= 80 else { throw LibraryFailure.invalid("invalid alerts query") }
        return try withDB { db in
            var clauses = ["1=1"], args: [String] = []
            if let name = query["name"], !name.isEmpty { clauses.append("m.alert=?"); args.append(name) }
            if query["unread"] != "false" { clauses.append("m.seen_seq<m.seq") }
            let rows = try run(db, "SELECT m.alert,m.story_id,m.seq,m.revision,m.subscription_version,m.seen_seq,s.payload FROM watch_matches m JOIN stories s ON s.id=m.story_id WHERE " + clauses.joined(separator: " AND ") + " ORDER BY m.seq DESC,m.alert,m.story_id LIMIT ? OFFSET ?", args + [String(limit+1),String(offset)])
            let subscriptions: [[String: Any]] = try run(db, "SELECT name,payload FROM watch_alerts ORDER BY name").map { row in
                var value = try decode(row[1]); value["name"] = row[0]; return value
            }
            let matches: [[String: Any]] = try rows.prefix(limit).map { row in
                let story = try decode(row[6]), seq = Int(row[2]) ?? 0
                return ["name":row[0],"story_id":row[1],"seq":seq,"matched_revision":Int(row[3]) ?? 0,
                        "subscription_version":Int(row[4]) ?? 0,"unread":(Int(row[5]) ?? 0)<seq,
                        "title":story["title"] ?? "","current_revision":story["revision"] ?? 0,
                        "merged_into":story["merged_into"] ?? NSNull()]
            }
            return ["subscriptions":subscriptions,"matches":matches,
                    "unread_count":Int(try run(db,"SELECT count(*) FROM watch_matches WHERE seen_seq<seq")[0][0]) ?? 0,
                    "limit":limit,"offset":offset,"next_offset":rows.count>limit ? offset+limit : NSNull()]
        }
    }
    func alertChange(_ change: [String: Any]) throws -> [String: Any] {
        guard let action = change["action"] as? String, ["subscribe","delete","mark_seen"].contains(action),
              let rawName = change["name"] as? String else { throw LibraryFailure.invalid("invalid alert command") }
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.unicodeScalars.count <= 80 else { throw LibraryFailure.invalid("name must be 1-80 characters") }
        let allowed: Set<String> = action == "mark_seen" ? ["action","name","story_id","expected_seq"] : action == "subscribe" ? ["action","name","expected_version","filters","enabled"] : ["action","name","expected_version"]
        guard Set(change.keys).isSubset(of: allowed) else { throw LibraryFailure.invalid("invalid alert command") }
        return try withDB { db in
            if action == "mark_seen" {
                guard let id = change["story_id"] as? String, !id.isEmpty, id.unicodeScalars.count <= 100,
                      let seq = ConsumerSchema.integer(change["expected_seq"]), seq>0 else { throw LibraryFailure.invalid("story_id and positive expected_seq required") }
                guard let row = try run(db,"SELECT seq FROM watch_matches WHERE alert=? AND story_id=?",[name,id]).first else { throw LibraryFailure.missing }
                guard seq == Int(row[0]) else { throw LibraryFailure.conflict("a newer match arrived; reload before marking seen") }
                try run(db,"UPDATE watch_matches SET seen_seq=seq WHERE alert=? AND story_id=?",[name,id])
                return ["name":name,"story_id":id,"seen_seq":seq]
            }
            let row = try run(db,"SELECT payload FROM watch_alerts WHERE name=?",[name]).first
            let prior = try row.map { try decode($0[0]) } ?? [:]
            guard let expected = ConsumerSchema.integer(change["expected_version"]), expected>=0 else { throw LibraryFailure.invalid("nonnegative expected_version required") }
            guard expected == (prior["version"] as? Int ?? 0) else { throw LibraryFailure.conflict("subscription changed; reload before saving") }
            if action == "delete" {
                guard row != nil else { throw LibraryFailure.missing }
                try run(db,"DELETE FROM watch_matches WHERE alert=?",[name])
                try run(db,"DELETE FROM watch_alerts WHERE name=?",[name])
                return ["name":name,"deleted":true]
            }
            guard let filters = change["filters"] as? [String: String], let enabled = change["enabled"] as? NSNumber,
                  CFGetTypeID(enabled) == CFBooleanGetTypeID() else { throw LibraryFailure.invalid("filters and boolean enabled required") }
            _ = try AlertSchema.query(filters)
            if row == nil, (Int(try run(db,"SELECT count(*) FROM watch_alerts")[0][0]) ?? 0)>=32 { throw LibraryFailure.conflict("32 subscriptions maximum; remove an unused subscription first") }
            try run(db,"INSERT OR IGNORE INTO watch_clock VALUES (1,0)")
            try run(db,"UPDATE watch_clock SET value=value+1 WHERE id=1")
            guard let version = Int(try run(db,"SELECT value FROM watch_clock WHERE id=1")[0][0]) else { throw Failure.database }
            let value: [String: Any] = ["filters":filters,"enabled":enabled.boolValue,"version":version,
                "start_seq":Int(try run(db,"SELECT coalesce(max(seq),0) FROM events")[0][0]) ?? 0]
            try run(db,"INSERT OR REPLACE INTO watch_alerts VALUES (?,?)",[name,try encode(value)])
            return value.merging(["name":name]) { _, new in new }
        }
    }
}
