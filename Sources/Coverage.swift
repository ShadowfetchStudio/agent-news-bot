import Foundation

struct SourceCheck {
    var source: String
    var provider = "direct"
    var state = "success"
    var detail = ""
    var checkedAt = ISO8601DateFormatter().string(from: Date())
    var wire: [String: Any] {
        ["source": source, "provider": provider, "state": state, "detail": detail, "checked_at": checkedAt]
    }
}

enum Coverage {
    // Success means a response was fetched and parsed, not that every claim is verified.
    static func summary(_ checks: [SourceCheck]) -> [String: Any] {
        let unique = Dictionary(checks.map { ($0.provider + ":" + $0.source, $0) }, uniquingKeysWith: { _, newer in newer })
        let rows = unique.values.sorted { ($0.provider, $0.source) < ($1.provider, $1.source) }
        let success = rows.filter { $0.state == "success" }.count
        let state: String
        if rows.isEmpty { state = "unknown" }
        else if success == rows.count { state = "success" }
        else if success > 0 || rows.contains(where: { $0.state == "partial" }) { state = "partial" }
        else if rows.contains(where: { $0.state == "stale" }) { state = "stale" }
        else { state = "unavailable" }
        return ["state": state, "checked": rows.count, "successful": success,
                "degraded": rows.count - success, "sources": rows.map(\.wire)]
    }
}

extension DeskResult {
    var coverageChecks: [SourceCheck] {
        // A legacy collector's errors must still degrade coverage; absence of checks is unknown.
        sourceChecks + (sourceErrors.isEmpty || sourceChecks.contains(where: { $0.state != "success" }) ? [] :
            [SourceCheck(source: desk.title + " warnings", state: "unavailable", detail: sourceErrors.joined(separator: "; "))])
    }
    var wire: [String: Any] {
        let carried = articles.filter { $0.wire["carried"] as? Bool == true }.count
        let updated = articles.filter { $0.wire["carried"] as? Bool != true && ["updated", "enriched", "corrected"].contains($0.wire["change"] as? String ?? "") }.count
        let fresh = articles.count - carried
        return ["desk": desk.rawValue, "title": desk.title, "fresh": fresh, "new": fresh - updated,
                "updated": updated, "carried": carried, "displayed": articles.count,
                "skipped_old": skippedOld, "skipped_dup": skippedDup, "errors": sourceErrors,
                "coverage": Coverage.summary(coverageChecks)]
    }
}
