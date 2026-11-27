import Foundation
import SQLite3

enum ConsumerFailure: Error { case invalid(String), conflict(String), missing }
enum ConsumerSchema {
    static let sql = "CREATE TABLE IF NOT EXISTS consumer_meta(name TEXT PRIMARY KEY,generation INTEGER NOT NULL DEFAULT 0,last_ack REAL,lease_token TEXT NOT NULL DEFAULT '',lease_worker TEXT NOT NULL DEFAULT '',lease_until REAL NOT NULL DEFAULT 0)"
    static func integer(_ value: Any?) -> Int? {
        guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              !["d", "f"].contains(String(cString: value.objCType)) else { return nil }
        return value.intValue
    }
}

extension StoryStore {
    func deliveryGuard(_ db: OpaquePointer, consumer: String, generation: Int? = nil, lease: String? = nil, ack: Bool = false) throws -> Int {
        try run(db, "INSERT OR IGNORE INTO consumer_meta(name) VALUES (?)", [consumer])
        let row = try run(db, "SELECT generation,lease_token,lease_until FROM consumer_meta WHERE name=?", [consumer])[0]
        let gen = Int(row[0]) ?? 0, until = Double(row[2]) ?? 0
        if let generation, generation != gen { throw ConsumerFailure.conflict("consumer generation changed; fetch again") }
        if ack && gen > 0 && generation == nil { throw ConsumerFailure.conflict("acknowledgement requires the current generation") }
        if lease != nil || until > Date().timeIntervalSince1970 {
            guard let lease, !lease.isEmpty, until > Date().timeIntervalSince1970, lease == row[1] else { throw ConsumerFailure.conflict("active matching worker lease required") }
        }
        return gen
    }
    func consumerStatus() throws -> [String: Any] {
        try withDB { db in
            let rows = try run(db, "SELECT c.name,c.cursor,c.offered,coalesce(m.generation,0),m.last_ack,coalesce(m.lease_worker,''),coalesce(m.lease_until,0),(SELECT count(*) FROM events WHERE seq>c.cursor) FROM consumers c LEFT JOIN consumer_meta m ON m.name=c.name ORDER BY c.name")
            let latest = Int(try run(db, "SELECT coalesce(max(seq),0) FROM events")[0][0]) ?? 0
            let consumers: [[String: Any]] = rows.map { row in
                let until = Double(row[6]) ?? 0, active = until > Date().timeIntervalSince1970
                return ["consumer": row[0], "cursor": Int(row[1]) ?? 0, "offered": Int(row[2]) ?? 0, "generation": Int(row[3]) ?? 0,
                        "last_ack_at": Double(row[4]).map { $0 as Any } ?? NSNull(), "lease_worker": active ? row[5] as Any : NSNull(),
                        "lease_expires_at": active ? until as Any : NSNull(), "lag": Int(row[7]) ?? 0]
            }
            return ["latest_seq": latest, "retention": try publicRetention(db), "consumers": consumers]
        }
    }
    func consumerChange(_ change: [String: Any]) throws -> [String: Any] {
        guard Set(change.keys).isSubset(of: ["consumer", "action", "start", "since", "expected_generation", "worker", "lease", "ttl_seconds"]),
              let name = change["consumer"] as? String, let action = change["action"] as? String,
              ["create", "reset", "lease", "renew", "release"].contains(action) else { throw ConsumerFailure.invalid("invalid consumer command") }
        try validate(name)
        return try withDB { db in
            let existing = try run(db, "SELECT cursor FROM consumers WHERE name=?", [name]).first
            if action == "create" && existing != nil { throw ConsumerFailure.conflict("consumer already exists; explicit reset required") }
            if action != "create" && existing == nil { throw ConsumerFailure.missing }
            try run(db, "INSERT OR IGNORE INTO consumer_meta(name) VALUES (?)", [name])
            let row = try run(db, "SELECT generation,lease_token,lease_worker,lease_until FROM consumer_meta WHERE name=?", [name])[0]
            var gen = Int(row[0]) ?? 0, token = row[1], worker = row[2]
            var until = Double(row[3]) ?? 0
            if action != "create" && ConsumerSchema.integer(change["expected_generation"]) != gen { throw ConsumerFailure.conflict("consumer generation changed; refresh status") }
            let now = Date().timeIntervalSince1970
            if ["create", "reset"].contains(action) {
                guard until <= now else { throw ConsumerFailure.conflict("release the active lease before reset") }
                let cursor: Int
                switch change["start"] as? String {
                case "history": cursor = 0
                case "retained": cursor = try retentionState(db)["pruned_through"] as? Int ?? 0
                case "now": cursor = Int(try run(db, "SELECT coalesce(max(seq),0) FROM events")[0][0]) ?? 0
                case "date":
                    guard (try retentionState(db)["pruned_through"] as? Int ?? 0) == 0 else {
                        throw ConsumerFailure.conflict("date replay is unavailable after archiving; choose retained or now explicitly")
                    }
                    guard let value = change["since"] as? String else { throw ConsumerFailure.invalid("since requires an ISO-8601 timestamp with timezone") }
                    let formatter = ISO8601DateFormatter()
                    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                    var date = formatter.date(from: value)
                    if date == nil { formatter.formatOptions = [.withInternetDateTime]; date = formatter.date(from: value) }
                    guard let date else { throw ConsumerFailure.invalid("invalid since timestamp") }
                    let candidate = try run(db, "SELECT min(seq)-1 FROM events WHERE julianday(json_extract(payload,'$.updated_at'))>=julianday(?,'unixepoch')", [String(date.timeIntervalSince1970)])[0][0]
                    if let first = Int(candidate) { cursor = first }
                    else { cursor = Int(try run(db, "SELECT coalesce(max(seq),0) FROM events")[0][0]) ?? 0 }
                default: throw ConsumerFailure.invalid("start must be history, retained, now or date")
                }
                _ = try retainedGuard(db, cursor: cursor)
                gen += 1
                try run(db, "INSERT OR REPLACE INTO consumers(name,cursor,offered) VALUES (?,?,?)", [name,String(cursor),String(cursor)])
                try run(db, "UPDATE consumer_meta SET generation=?,last_ack=NULL,lease_token='',lease_worker='',lease_until=0 WHERE name=?", [String(gen),name])
                return ["consumer": name, "cursor": cursor, "generation": gen]
            }
            if action == "lease" {
                guard until <= now else { throw ConsumerFailure.conflict("consumer already leased; renew using its token") }
                guard let value = change["worker"] as? String else { throw ConsumerFailure.invalid("worker required") }
                try validate(value); worker = value; token = UUID().uuidString + UUID().uuidString; gen += 1
            } else {
                _ = try deliveryGuard(db, consumer: name, generation: gen, lease: change["lease"] as? String, ack: true)
                guard until > now else { throw ConsumerFailure.conflict("lease expired; acquire a new lease") }
            }
            if action == "release" {
                try run(db, "UPDATE consumer_meta SET lease_token='',lease_worker='',lease_until=0,generation=generation+1 WHERE name=?", [name])
                try run(db, "UPDATE consumers SET offered=cursor WHERE name=?", [name])
                return ["consumer": name, "generation": gen + 1, "released": true]
            }
            guard let ttl = ConsumerSchema.integer(change["ttl_seconds"] ?? 60), (10...300).contains(ttl) else { throw ConsumerFailure.invalid("ttl_seconds must be 10-300") }
            until = now + Double(ttl)
            try run(db, "UPDATE consumer_meta SET generation=?,lease_token=?,lease_worker=?,lease_until=? WHERE name=?", [String(gen),token,worker,String(until),name])
            if action == "lease" { try run(db, "UPDATE consumers SET offered=cursor WHERE name=?", [name]) }
            return ["consumer": name, "generation": gen, "lease": token, "worker": worker, "lease_expires_at": until]
        }
    }
}
