import SwiftUI

struct CoverageView: View {
    @EnvironmentObject private var engine: NewsEngine
    @Environment(\.dismiss) private var dismiss
    @State private var history: [[String: Any]] = []
    @State private var historyError = ""
    @State private var reloadToken = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Operations and coverage").font(.title2.bold())
                Spacer()
                Button("Reload all") { reloadToken += 1 }
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("Results of the last refresh. A successful response means it was fetched and parsed; it does not verify the reporting.")
                .font(.callout).foregroundStyle(.secondary)
            if let report = engine.report {
                Text("Checked \(report.finished.formatted(date: .abbreviated, time: .standard)) · \(engine.coverageState)").font(.callout.bold())
            }
            if engine.desks.isEmpty {
                Text("No source checks yet. Run Fetch Fresh to check coverage.").padding(.vertical)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    OperatorControlsView(reloadToken: reloadToken).environmentObject(engine)
                    if !historyError.isEmpty { Text(historyError).foregroundStyle(.red) }
                    if !history.isEmpty {
                        DisclosureGroup("Persistent source history (\(history.count))") {
                            Text("Historical checks, not current uptime. Retry times and active probes are read when this view is loaded. A successful check means transport and parsing completed; it does not verify reporting.")
                                .font(.caption).foregroundStyle(.secondary)
                            ForEach(history.compactMap { row in
                                (row["id"] as? String).map { HealthRow(id:$0, fields:row) }
                            }) { entry in
                                let row = entry.fields
                                VStack(alignment:.leading,spacing:4) {
                                    Text("\(row["source"] as? String ?? "Source") · \(row["circuit"] as? String ?? "unknown")").font(.headline)
                                    Text("\(row["provider"] as? String ?? "direct") · \(String((row["id"] as? String ?? "").prefix(8))) · \(row["last_state"] as? String ?? "unknown") · failure streak \(row["failure_streak"] as? Int ?? 0)")
                                    Text("Last attempt: \(timestamp(row["last_attempt"]))\nLast fully successful check: \(timestamp(row["last_success"]))\nLast usable response: \(timestamp(row["last_usable"]))")
                                    if (row["next_retry"] as? Double ?? 0) > Date().timeIntervalSince1970 { Text("Next retry no earlier than \(timestamp(row["next_retry"]))") }
                                    if row["circuit"] as? String == "probing" { Text("Probe lease expires \(timestamp(row["lease_until"]))") }
                                    if let error = row["last_error"] as? String, !error.isEmpty { Text("Last failure: " + error) }
                                }.font(.caption).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading).padding(10).anbCard()
                                    .accessibilityElement(children:.contain)
                                    .accessibilityLabel((row["source"] as? String ?? "Source") + " source health")
                            }
                        }
                    }
                    ForEach(engine.desks) { result in
                        let counts = result.wire
                        VStack(alignment: .leading, spacing: 6) {
                            Text(result.desk.title).font(.headline)
                            Text("New: \(counts["new"] as? Int ?? 0) · Updated: \(counts["updated"] as? Int ?? 0) · Carried: \(counts["carried"] as? Int ?? 0) · Displayed: \(result.articles.count)")
                                .font(.caption.monospacedDigit())
                            ForEach(Array(result.coverageChecks.enumerated()), id: \.offset) { _, check in
                                HStack(alignment: .top) {
                                    Image(systemName: check.state == "success" ? "checkmark.circle" : "exclamationmark.triangle")
                                        .foregroundStyle(check.state == "success" ? Theme.success : Theme.warning)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text("\(check.source) · \(check.state)").font(.callout)
                                        if !check.detail.isEmpty { Text(check.detail).font(.caption).foregroundStyle(.secondary) }
                                    }
                                }
                            }
                            if result.coverageChecks.isEmpty { Text("No coverage evidence available.").font(.caption) }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(12).anbCard()
                    }
                }
            }
        }.padding(20).frame(minWidth: 620, minHeight: 480)
            .task(id: reloadToken) { await reloadHistory() }
    }
    @MainActor private func reloadHistory() async {
        let health = engine.store.health
        do {
            let data = try await Task.detached { try JSONSerialization.data(withJSONObject: health.list()) }.value
            history = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] ?? []
            historyError = ""
        } catch { historyError = "Source history could not be read. Check local storage." }
    }
    private func timestamp(_ value: Any?) -> String {
        guard let seconds = value as? Double, seconds > 0 else { return "Never" }
        return Date(timeIntervalSince1970:seconds).formatted(date:.abbreviated,time:.standard)
    }
}

private struct HealthRow: Identifiable { let id: String; let fields: [String: Any] }
