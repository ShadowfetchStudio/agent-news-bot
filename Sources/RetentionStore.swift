import Foundation
import SQLite3

enum RetentionSchema {
    static let sql = "CREATE TABLE IF NOT EXISTS journal_retention(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)"
}

extension StoryStore {
    func retentionState(_ db: OpaquePointer) throws -> [String: Any] {
        var state = try run(db, "SELECT payload FROM journal_retention WHERE id=1").first.map { try decode($0[0]) } ?? [:]
        guard let floor = ConsumerSchema.integer(state["pruned_through"] ?? 0), floor >= 0 else { throw Failure.database }
        state["pruned_through"] = floor
        return state
    }
    func retainedGuard(_ db: OpaquePointer, cursor: Int) throws -> Int {
        let floor = try retentionState(db)["pruned_through"] as? Int ?? 0
        guard cursor >= floor else { throw ConsumerFailure.conflict("history was archived; explicitly create/reset this consumer from retained or now") }
        return floor
    }
    func publicRetention(_ db: OpaquePointer) throws -> [String: Any] {
        let state = try retentionState(db)
        return ["pruned_through": state["pruned_through"] ?? 0, "pruned_events": state["pruned_events"] ?? 0,
                "last_pruned_at": state["last_pruned_at"] ?? NSNull()]
    }
}
