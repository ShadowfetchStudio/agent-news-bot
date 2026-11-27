import SwiftUI

/// Displays retained text literally; source content cannot supply view markup or actions.
struct EditorialBriefView: View {
    let brief: [String: Any]
    private func rows(_ key: String) -> [[String: Any]] { brief[key] as? [[String: Any]] ?? [] }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Editorial brief").font(.title2.bold())
                Text("Saved story revision \(brief["story_revision"] as? Int ?? 0) · draft version \(brief["editorial_version"] as? Int ?? 0)").font(.caption).foregroundStyle(.secondary)
                Text(verbatim: brief["scope"] as? String ?? "").font(.callout)
                section("Retained report") {
                    Text(verbatim: brief["title"] as? String ?? "").font(.headline)
                    Text(verbatim: brief["retained_summary"] as? String ?? "")
                }
                section("Why review this story") {
                    let priority = brief["priority"] as? [String: Any] ?? [:]
                    Text("\(priority["score"] as? Int ?? 0) review-priority points. Not a truth or newsworthiness score.")
                    ForEach(Array((priority["components"] as? [[String: Any]] ?? []).enumerated()), id: \.offset) { _, row in
                        Text("\(row["points"] as? Int ?? 0): \(row["reason"] as? String ?? "")")
                    }
                }
                section("What changed") {
                    Text((brief["previous_revision"] as? Int).map { "Compared with revision \($0)." } ?? "No earlier retained event for comparison.")
                    ForEach(Array(rows("changes").enumerated()), id: \.offset) { _, row in
                        Text(verbatim: (row["field"] as? String ?? "").capitalized).font(.headline)
                        Text(verbatim: "Before: " + (row["before"] as? String ?? ""))
                        Text(verbatim: "After: " + (row["after"] as? String ?? ""))
                    }
                    ForEach(["added_sources", "removed_sources"], id: \.self) { key in
                        ForEach(brief[key] as? [String] ?? [], id: \.self) { url in
                            Text(verbatim: (key == "added_sources" ? "Added: " : "Removed: ") + url)
                        }
                    }
                }
                section("Source evidence") {
                    ForEach(Array(rows("evidence").enumerated()), id: \.offset) { _, source in
                        BriefSourceView(source: source)
                    }
                }
                section("Differences to resolve") {
                    if rows("differences").isEmpty { Text("No difference flag is recorded; agreement and factual accuracy have not been established.") }
                    ForEach(Array(rows("differences").enumerated()), id: \.offset) { _, row in
                        Text(verbatim: (row["source_ids"] as? [String] ?? []).joined(separator: ", ") + ": " + (row["note"] as? String ?? ""))
                    }
                }
                section("Unknowns") { ForEach(brief["unknowns"] as? [String] ?? [], id: \.self) { Text(verbatim: "• " + $0) } }
                section("Suggested follow-up") {
                    ForEach(Array(rows("follow_up").enumerated()), id: \.offset) { _, row in
                        Text(verbatim: "• " + (row["question"] as? String ?? "") + ((row["source_ids"] as? [String] ?? []).isEmpty ? "" : " [" + (row["source_ids"] as? [String] ?? []).joined(separator: ", ") + "]"))
                    }
                }
                section("Saved editorial notes") { Text(verbatim: brief["editorial_notes"] as? String ?? "") }
                if brief["truncated"] as? Bool == true { Text("This brief is excerpted. Inspect the complete retained research and event history before deciding.").foregroundStyle(.orange) }
            }.textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding()
        }
    }
    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) { Text(title).font(.headline); content() }
    }
}


private struct BriefSourceView: View {
    let source: [String: Any]
    @State private var expanded = false
    private var sid: String { source["id"] as? String ?? "" }
    private var publisher: String { source["publisher"] as? String ?? "" }
    private var observations: [[String: Any]] { source["observations"] as? [[String: Any]] ?? [] }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { expanded.toggle() } label: {
                HStack {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right").accessibilityHidden(true)
                    Text(verbatim: sid + " — " + publisher)
                }
            }.buttonStyle(.plain)
            .accessibilityLabel(Text("Source " + sid + ", " + publisher))
            .accessibilityValue(Text(expanded ? "Expanded" : "Collapsed"))
            if expanded {
                Text(verbatim: source["title"] as? String ?? "").font(.headline)
                Text(verbatim: source["summary"] as? String ?? "")
                Text(verbatim: "Published: " + (source["published"] as? String ?? "unknown"))
                let raw = source["url"] as? String ?? ""
                if let url = ArticleURL.validated(raw) { Link(raw, destination: url) }
                else { Text(verbatim: raw) }
                ForEach(Array(observations.enumerated()), id: \.offset) { _, observation in
                    Text(verbatim: observationHeading(observation)).font(.headline)
                    Text(verbatim: observation["summary"] as? String ?? "")
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func observationHeading(_ row: [String: Any]) -> String {
        "Provider " + (row["provider"] as? String ?? "") + ": " + (row["title"] as? String ?? "")
    }
}
