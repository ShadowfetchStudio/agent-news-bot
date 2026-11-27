import Foundation

// Display-state policy only. Carrying a story never creates a journal revision or acknowledges a consumer.
enum SnapshotPolicy {
    static func merge(previous: [Article], fresh: [Article], previousFetchedAt: Date?, lookback: Double,
                      enabled: Set<Desk>, limit: Int, now: Date = Date()) -> [Article] {
        let cutoff = now.addingTimeInterval(-max(0, lookback) * 3600)
        var seen = Set<String>(), retained: [Article] = []
        for (rows, carried) in [(fresh, false), (previous, true)] {
            for article in rows {
                guard !seen.contains(article.id), article.desks.contains(where: enabled.contains) else { continue }
                var wire = article.wire
                let firstSeen = DateStamp.parse(wire["first_seen"] as? String ?? "") ?? (carried ? previousFetchedAt : nil)
                guard let date = article.published ?? firstSeen,
                      date >= cutoff, date <= now.addingTimeInterval(300) else { continue }
                wire["carried"] = carried
                if wire["first_seen"] == nil, let firstSeen { wire["first_seen"] = ISO8601DateFormatter().string(from: firstSeen) }
                // Refresh legacy draft lengths without changing immutable journal events.
                let (tweet, count) = Hashtagger.tweet(title: article.title, url: article.url, tags: article.hashtags)
                wire["tweet"] = tweet; wire["tweet_chars"] = count
                guard let item = try? Article.fromWire(wire) else { continue }
                seen.insert(item.id); retained.append(item)
            }
        }
        retained.sort {
            let left = $0.published ?? DateStamp.parse($0.wire["first_seen"] as? String ?? "") ?? .distantPast
            let right = $1.published ?? DateStamp.parse($1.wire["first_seen"] as? String ?? "") ?? .distantPast
            return left == right ? $0.id < $1.id : left > right
        }
        // Allocate each desk independently. Preserve full journal membership even
        // when a shared story only fits in one desk's remaining display slots.
        var counts: [Desk: Int] = [:]
        return retained.compactMap { article in
            let display = Set(article.desks).filter { enabled.contains($0) && counts[$0, default: 0] < max(0, limit) }
                .sorted { $0.rawValue < $1.rawValue }
            guard !display.isEmpty else { return nil }
            var wire = article.wire
            wire["display_desks"] = display.map(\.rawValue)
            guard let row = try? Article.fromWire(wire) else { return nil }
            for desk in display { counts[desk, default: 0] += 1 }
            return row
        }
    }

    static func restore(_ payload: [String: Any], lookback: Double, enabled: Set<Desk>, limit: Int, now: Date = Date()) -> (articles: [Article], desks: [DeskResult], fetchedAt: String?) {
        let fetchedAt = payload["fetched_at"] as? String
        let rows = (payload["articles"] as? [[String: Any]] ?? []).compactMap { try? Article.fromWire($0) }
        let articles = merge(previous: rows, fresh: [], previousFetchedAt: fetchedAt.flatMap(DateStamp.parse), lookback: lookback, enabled: enabled, limit: limit, now: now)
        var desks: [DeskResult] = []
        for raw in payload["desks"] as? [[String: Any]] ?? [] {
            guard let desk = Desk(rawValue: raw["desk"] as? String ?? ""), enabled.contains(desk) else { continue }
            let coverage = raw["coverage"] as? [String: Any] ?? [:]
            let checks = (coverage["sources"] as? [[String: Any]] ?? []).map { source -> SourceCheck in
                let state = source["state"] as? String ?? "unknown"
                return SourceCheck(source: source["source"] as? String ?? desk.title, provider: source["provider"] as? String ?? "direct",
                    state: ["success", "partial"].contains(state) ? "stale" : state,
                    detail: "Restored snapshot; not checked in this session. " + (source["detail"] as? String ?? ""),
                    checkedAt: source["checked_at"] as? String ?? fetchedAt ?? "", health: source["health"] as? [String: Any] ?? [:])
            }
            desks.append(DeskResult(desk: desk, articles: articles.filter { $0.displayDesks.contains(desk) }, skippedOld: 0, skippedDup: 0,
                sourceErrors: raw["errors"] as? [String] ?? [], sourceChecks: checks))
        }
        return (articles, desks, fetchedAt)
    }
}
