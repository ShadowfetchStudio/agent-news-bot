import SwiftUI

private struct StoryHistoryEntry: Identifiable, Sendable {
    let id: Int
    let revision: Int
    let gap: Bool
    let change: String
    let time: String
    let oldTitle: String?
    let title: String
    let oldSummary: String?
    let summary: String?
    let fields: [String]
}

struct StoryHistoryView: View {
    let store: StoryStore
    let storyID: String
    let revision: Int
    @State private var entries: [StoryHistoryEntry] = []
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Story timeline").font(.headline)
            Text("Recorded revisions, newest first. Provider observations are reports, not independent verification.")
                .font(.caption).foregroundStyle(Theme.textLow)
            if !error.isEmpty { Text(error).foregroundStyle(Theme.warning) }
            ForEach(entries) { entry in
                DisclosureGroup("Revision \(entry.revision) · \(entry.change.capitalized)") {
                    VStack(alignment: .leading, spacing: 8) {
                        if entry.gap {
                            Text("Earlier revisions are outside this retained page. No adjacent-revision comparison is shown; consult your research archive for archived history.")
                                .font(.caption).foregroundStyle(Theme.warning)
                        }
                        Text(entry.time).font(.caption).foregroundStyle(Theme.textLow)
                        if let old = entry.oldTitle, old != entry.title {
                            Text("Earlier headline").font(.caption.bold())
                            Text(old).foregroundStyle(Theme.textMed)
                            Text("Updated headline").font(.caption.bold())
                        }
                        Text(entry.title)
                        if let summary = entry.summary {
                            if let old = entry.oldSummary {
                                Text("Earlier summary").font(.caption.bold())
                                Text(old).foregroundStyle(Theme.textMed)
                            }
                            Text("Updated summary").font(.caption.bold())
                            Text(summary)
                        }
                        Text("Changed: " + entry.fields.joined(separator: ", "))
                            .font(.caption).foregroundStyle(Theme.textLow)
                    }.padding(.vertical, 6).textSelection(.enabled)
                }
            }
        }
        .padding(10).anbCard()
        .task(id: "\(storyID):\(revision)") {
            let db = store, id = storyID
            do {
                let loaded = try await Task.detached(priority: .utility) {
                    try db.revisions(id).reversed().map { row -> StoryHistoryEntry in
                        let story = row["story"] as? [String: Any] ?? [:]
                        let diff = row["diff"] as? [String: [String: Any]] ?? [:]
                        return StoryHistoryEntry(id: row["seq"] as? Int ?? 0,
                            revision: story["revision"] as? Int ?? 0,
                            gap: row["history_gap_before"] as? Bool ?? false,
                            change: story["change"] as? String ?? "recorded",
                            time: story["updated_at"] as? String ?? "",
                            oldTitle: diff["title"]?["before"] as? String,
                            title: story["title"] as? String ?? "",
                            oldSummary: diff["summary"]?["before"] as? String,
                            summary: (diff["summary"]?["after"] ?? story["summary"]) as? String,
                            fields: diff.keys.sorted())
                    }
                }.value
                guard !Task.isCancelled else { return }
                entries = loaded; error = ""
            } catch { self.error = "Story history could not be loaded. Try again after the current fetch." }
        }
    }
}
