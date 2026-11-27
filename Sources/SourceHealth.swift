import Foundation
import SQLite3
import CryptoKit

final class SourceAttempt: @unchecked Sendable {
    @TaskLocal static var current: SourceAttempt?
    private let lock = NSLock()
    private var errors = 0
    private var retryAt = 0.0
    private var code = ""
    func record(_ code: String, retryAfter: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        errors += 1; self.code = code
        retryAt = max(retryAt, SourceHealth.retryTime(retryAfter, now: Date().timeIntervalSince1970))
    }
    func check() throws {
        lock.lock(); defer { lock.unlock() }
        if errors >= 3 || retryAt > Date().timeIntervalSince1970 { throw SourceHealth.Failure.paused }
    }
    var failure: (String, Double) {
        lock.lock(); defer { lock.unlock() }; return (code, retryAt)
    }
}

final class SourceHealth: @unchecked Sendable {
    enum Failure: Error { case storage, capacity, paused }
    let path: URL
    init(path: URL) { self.path = path }
    static var shared: SourceHealth { SourceHealth(path: AppPaths.support.appendingPathComponent("source-health.sqlite")) }
    static func identity(_ key: String) -> String { SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined() }
    static func retryTime(_ raw: String?, now: Double) -> Double {
        guard let raw else { return 0 }
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty, value.allSatisfy({ $0 >= "0" && $0 <= "9" }), let seconds = Double(value) {
            return now + min(86400, max(0, seconds))
        }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0); formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss z"
        guard let date = formatter.date(from: value) else { return 0 }
        return now + min(86400, max(0, date.timeIntervalSince1970 - now))
    }
    private func transaction<T>(_ body: (OpaquePointer) throws -> T) throws -> T {
        try AppPaths.prepareDatabase(path)
        var handle: OpaquePointer?
        guard sqlite3_open(path.path, &handle) == SQLITE_OK, let db = handle else {
            if let handle { sqlite3_close(handle) }; throw Failure.storage
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 5000)
        guard sqlite3_exec(db, "CREATE TABLE IF NOT EXISTS source_health (id TEXT PRIMARY KEY, payload TEXT NOT NULL); BEGIN IMMEDIATE", nil,nil,nil) == SQLITE_OK else { throw Failure.storage }
        do {
            let result = try body(db)
            guard sqlite3_exec(db,"COMMIT",nil,nil,nil) == SQLITE_OK else { throw Failure.storage }
            return result
        } catch { sqlite3_exec(db,"ROLLBACK",nil,nil,nil); throw error }
    }
    private func query(_ db: OpaquePointer, _ sql: String, _ values: [String] = []) throws -> [String] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db,sql,-1,&stmt,nil) == SQLITE_OK else { throw Failure.storage }
        defer { sqlite3_finalize(stmt) }
        for (i,value) in values.enumerated() { sqlite3_bind_text(stmt,Int32(i+1),value,-1,unsafeBitCast(-1,to:sqlite3_destructor_type.self)) }
        var rows: [String] = []
        while true {
            let status = sqlite3_step(stmt)
            if status == SQLITE_DONE { return rows }
            guard status == SQLITE_ROW else { throw Failure.storage }
            rows.append(sqlite3_column_text(stmt,0).map { String(cString:$0) } ?? "")
        }
    }
    private func decode(_ value: String) throws -> [String: Any] {
        guard let row = try JSONSerialization.jsonObject(with:Data(value.utf8)) as? [String: Any] else { throw Failure.storage }
        for key in ["id","source","provider","lease","last_state","last_error"] {
            guard let value = row[key] as? String, value.count <= 200 else { throw Failure.storage }
        }
        guard (row["id"] as! String).range(of:"^[a-f0-9]{64}$",options:.regularExpression) != nil,
              (row["lease"] as! String).range(of:"^([a-f0-9]{32})?$",options:.regularExpression) != nil else { throw Failure.storage }
        for key in ["last_attempt","last_success","last_usable","failure_streak","next_retry","lease_until"] {
            guard let value = row[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
                  value.doubleValue.isFinite, (0...1e12).contains(value.doubleValue) else { throw Failure.storage }
        }
        let streak = number(row,"failure_streak")
        guard streak <= 30, streak.rounded() == streak else { throw Failure.storage }
        return row
    }
    private func put(_ db: OpaquePointer, _ row: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        _ = try query(db,"INSERT OR REPLACE INTO source_health VALUES (?,?)",[row["id"] as! String,String(decoding:data,as:UTF8.self)])
    }
    private func number(_ row: [String: Any], _ key: String) -> Double { (row[key] as? NSNumber)?.doubleValue ?? 0 }
    private func publicRow(_ original: [String: Any], now: Double) -> [String: Any] {
        var row = original; row.removeValue(forKey:"lease")
        row["circuit"] = number(row,"lease_until") > now ? "probing" : number(row,"next_retry") > now ?
            (number(row,"failure_streak") >= 3 ? "open" : "backoff") : "ready"
        return row
    }
    func list(now: Double = Date().timeIntervalSince1970) throws -> [[String: Any]] {
        guard FileManager.default.fileExists(atPath:path.path) else { return [] }
        return try transaction { db in
            try query(db,"SELECT payload FROM source_health LIMIT 1025").map { publicRow(try decode($0),now:now) }
                .sorted { ($0["provider"] as? String ?? "",$0["source"] as? String ?? "",$0["id"] as? String ?? "") < ($1["provider"] as? String ?? "",$1["source"] as? String ?? "",$1["id"] as? String ?? "") }
        }
    }
    func begin(key: String, source: String, provider: String = "direct", now: Double = Date().timeIntervalSince1970) throws -> (String?,[String: Any]) {
        try transaction { db in
            let id = Self.identity(key)
            let existing = try query(db,"SELECT payload FROM source_health WHERE id=?",[id]).first
            var row: [String: Any]
            if let existing {
                row = try decode(existing)
                if max(number(row,"next_retry"),number(row,"lease_until")) > now { return (nil,publicRow(row,now:now)) }
            } else {
                guard Int(try query(db,"SELECT count(*) FROM source_health").first ?? "1024")! < 1024 else { throw Failure.capacity }
                row = ["id":id,"source":String(source.prefix(200)),"provider":String(provider.prefix(40)),"last_attempt":0,"last_success":0,"last_usable":0,
                       "failure_streak":0,"next_retry":0,"lease_until":0,"lease":"","last_state":"unknown","last_error":""]
            }
            if !(row["lease"] as? String ?? "").isEmpty {
                row["failure_streak"] = min(30,number(row,"failure_streak")+1); row["last_error"] = "interrupted"; row["last_state"] = "unavailable"
            }
            let lease = UUID().uuidString.lowercased().replacingOccurrences(of:"-",with:"")
            row["lease"] = lease; row["lease_until"] = now+300; row["last_attempt"] = now
            try put(db,row); return (lease,publicRow(row,now:now))
        }
    }
    func finish(key: String, lease: String, state: String, code: String = "", retryAt: Double = 0, now: Double = Date().timeIntervalSince1970) throws -> [String: Any] {
        try transaction { db in
            guard let payload = try query(db,"SELECT payload FROM source_health WHERE id=?",[Self.identity(key)]).first else { throw Failure.storage }
            var row = try decode(payload)
            guard row["lease"] as? String == lease else { return publicRow(row,now:now) }
            row["lease"] = ""; row["lease_until"] = 0
            if state != "cancelled" {
                let success = state == "success"
                let healthyTransport = success || (state == "partial" && code.isEmpty && retryAt <= now)
                let streak = healthyTransport ? 0 : min(30,number(row,"failure_streak")+1)
                let delay = healthyTransport ? 0 : min(21600,60 * pow(2,min(streak-1,9)))
                row["last_state"] = state; row["failure_streak"] = streak
                row["last_error"] = healthyTransport ? "" : code.isEmpty ? "content_or_request" : String(code.prefix(80))
                row["next_retry"] = healthyTransport ? 0 : max(now+delay,min(now+86400,retryAt))
                if success { row["last_success"] = now }
                if ["success","partial"].contains(state) { row["last_usable"] = now }
            }
            try put(db,row); return publicRow(row,now:now)
        }
    }
    func run(key: String, source: String, desk: Desk, provider: String = "direct", operation: () async -> [DeskResult]) async -> [DeskResult] {
        func unavailable(_ message: String, history: [String: Any] = [:]) -> [DeskResult] {
            [DeskResult(desk:desk,articles:[],skippedOld:0,skippedDup:0,sourceErrors:[message],sourceChecks:[SourceCheck(source:source,provider:provider,state:"unavailable",detail:message,health:history)])]
        }
        do {
            let (lease,history) = try begin(key:key,source:source,provider:provider)
            guard let lease else { return unavailable("Source temporarily paused; see next retry and last-success history.",history:history) }
            let attempt = SourceAttempt()
            var results = await SourceAttempt.$current.withValue(attempt) { await operation() }
            let state = Task.isCancelled ? "cancelled" : Coverage.summary(results.flatMap(\.coverageChecks))["state"] as? String ?? "unknown"
            let (code,retryAt) = attempt.failure
            let updated: [String: Any]
            do { updated = try finish(key:key,lease:lease,state:state,code:code,retryAt:retryAt) }
            catch {
                for i in results.indices {
                    results[i].sourceErrors.append("Source health could not be saved; retrieved stories are retained.")
                    results[i].sourceChecks.append(SourceCheck(source:source,provider:provider,state:"partial",detail:"Source health storage failure"))
                }
                return results
            }
            for i in results.indices {
                if results[i].sourceChecks.isEmpty { results[i].sourceChecks = [SourceCheck(source:source,provider:provider,state:state)] }
                for j in results[i].sourceChecks.indices { results[i].sourceChecks[j].health = updated }
            }
            return results
        } catch { return unavailable("Source health storage unavailable; request or health update could not complete.") }
    }
}
