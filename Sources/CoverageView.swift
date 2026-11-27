import SwiftUI

struct CoverageView: View {
    @EnvironmentObject private var engine: NewsEngine
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Source coverage").font(.title2.bold())
                Spacer()
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
    }
}
