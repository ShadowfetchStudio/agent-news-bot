import Foundation
import Security

@main struct IntegrationHarness {
    static func main() async throws {
        let args = CommandLine.arguments
        if args[1] == "port-probe" { try output(["ready": await OpenClawClient.portOpen(args[2])]); return }
        if args[1] == "hermes-api" || args[1] == "hermes-probe" {
            let settings = await MainActor.run { () -> SettingsStore in
                let settings = SettingsStore.shared
                settings.hermesUseCLI = false
                settings.hermesAPIURL = args[2]
                return settings
            }
            if args[1] == "hermes-probe" {
                let status = await HermesClient.probe(settings: settings)
                try output(["ready": status.ready]); return
            }
            do { try output(["output": try await HermesClient.send(articles: [], settings: settings)]) }
            catch { try output(["error": "handoff failed"]) }
            return
        }
        if args[1] == "process" {
            let task = Task { try await AgentProcess.run(path: args[2], arguments: Array(args.dropFirst(5)), timeout: Double(args[3])!, maxOutput: Int(args[4])!) }
            if ProcessInfo.processInfo.environment["ANB_CANCEL_PROCESS"] == "1" {
                try await Task.sleep(nanoseconds: 100_000_000); task.cancel()
            }
            do { try output(["output": try await task.value]) }
            catch { try output(["error": error.localizedDescription, "cancelled": error is CancellationError]) }
            return
        }
        if args[1] == "snapshot" {
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [String: Any]
            let restored = SnapshotPolicy.restore(value, lookback: 12, enabled: [.science, .tech], limit: 20,
                now: Date(timeIntervalSince1970: 1795780800))
            try output(["articles": restored.articles.map(\.wire), "desks": restored.desks.map(\.wire), "fetched_at": restored.fetchedAt ?? ""]); return
        }
        if args[1] == "snapshot-merge" {
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [String: Any]
            let old = try (value["previous"] as! [[String: Any]]).map { try Article.fromWire($0) }
            let fresh = try (value["fresh"] as! [[String: Any]]).map { try Article.fromWire($0) }
            try output(["articles": SnapshotPolicy.merge(previous: old, fresh: fresh, previousFetchedAt: nil, lookback: 12,
                enabled: [.science, .tech], limit: 20, now: Date(timeIntervalSince1970: 1795780800)).map(\.wire)]); return
        }
        if args[1] == "paths" {
            try output(["support": AppPaths.support.path, "bundle": AppPaths.bundleId]); return
        }
        if args[1] == "desk-counts" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: Any]]
            let result = DeskResult(desk: .science, articles: try rows.map { try Article.fromWire($0) }, skippedOld: 0, skippedDup: 0, sourceErrors: [])
            try output(result.wire); return
        }
        if args[1] == "coverage" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: String]]
            let checks = rows.map { SourceCheck(source: $0["source"]!, provider: $0["provider"] ?? "direct", state: $0["state"]!, checkedAt: "fixture") }
            try output(Coverage.summary(checks)); return
        }
        if args[1] == "xtext" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: Any]]
            try output(["items": try rows.map { try XText.request($0) }]); return
        }
        if args[1] == "keychain-failure" {
            var deletes = 0, adds = 0
            let ops = Keychain.Operations(update: { _, _ in errSecAuthFailed },
                add: { _ in adds += 1; return errSecSuccess },
                delete: { _ in deletes += 1; return errSecSuccess },
                read: { _ in (errSecAuthFailed, nil) })
            var writeFailed = false, readFailed = false
            do { try Keychain.set("new secret", account: "fixture", using: ops) } catch { writeFailed = true }
            do { _ = try Keychain.get("fixture", using: ops) } catch { readFailed = true }
            try output(["write_failed": writeFailed, "read_failed": readFailed, "deletes": deletes, "adds": adds])
            return
        }
        if args[1] == "canonical-fixtures" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: String]]
            try output(["items": rows.map { ["canonical": CanonicalURL.fingerprint($0["raw"]!), "id": FreshnessStore.articleID(url: $0["raw"]!, fallback: "")] }]); return
        }
        if args[1] == "rss-parse" {
            do {
                let items = try RSSParser.parseChecked(data: Data(contentsOf: URL(fileURLWithPath: args[2])))
                try output(["items": items.map { ["title": $0.title, "url": $0.link, "summary": HTMLText.strip($0.summary)] }])
            } catch { try output(["error": "invalid feed"]) }
            return
        }
        if args[1] == "rss" {
            let store = FreshnessStore(path: URL(fileURLWithPath: args[3]))
            let result = await RSSClient.fetch(desk: .science, lookbackHours: Double(args[4])!, maxItems: Int(args[5])!, store: store, feeds: [FeedSource(url: args[2], label: "Fixture")])
            for update in result.cacheUpdates { store.saveFeedCache(update.url, etag: update.etag, lastModified: update.lastModified, body: update.body, observedAt: update.observedAt) }
            try output(["articles": result.articles.map(\.wire), "errors": result.sourceErrors, "coverage": Coverage.summary(result.coverageChecks)]); return
        }
        if args[1] == "fetch" {
            let results = await WorldMonitorClient.fetch(enabled: true, base: args[2], key: "test-fixture-key", variant: "tech", lookback: 100000, limit: 20)
            try output(["articles": results.flatMap(\.articles).map(\.wire), "errors": results.flatMap(\.sourceErrors), "coverage": Coverage.summary(results.flatMap(\.coverageChecks))])
            return
        }
        if args[1] == "digest" {
            let data = try Data(contentsOf: URL(fileURLWithPath: args[2]))
            let results = try WorldMonitorClient.parse(data: data, lookback: 12, limit: 20, now: Date(timeIntervalSince1970: 1795780800))
            try output(["articles": results.flatMap(\.articles).map(\.wire), "errors": results.flatMap(\.sourceErrors), "coverage": Coverage.summary(results.flatMap(\.coverageChecks))])
            return
        }
        if args[1] == "skill-install" {
            try output(["installed": SkillInstaller.install(args[3], at: URL(fileURLWithPath: args[2]))])
            return
        }
        if args[1] == "market-checkpoints" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: Any]]
            let articles = try rows.map { try Article.fromWire($0) }
            try output(["symbols": MarketsClient.checkpoints(for: articles, observations: [("AAA", "101"), ("BBB", "150")]).map { $0.0 }])
            return
        }
        let store = StoryStore(path: URL(fileURLWithPath: args[2]))
        if args[1] == "ingest" || args[1] == "ingest-error" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[3]))) as! [[String: Any]]
            let articles = try rows.map { try Article.fromWire($0) }
            if args[1] == "ingest-error" {
                do { _ = try store.ingest(articles); try output(["failed": false]) }
                catch { try output(["failed": true]) }
            } else { try output(["articles": try store.ingest(articles).map(\.wire)]) }
            return
        }
        if args[1] == "lifecycle" {
            let bridge = AgentBridge()
            let occupied = AgentBridge()
            let port = Int(args[3])!, other = Int(args[4])!
            guard occupied.start(port: other, token: "occupied") else { throw StoryStore.Failure.database }
            var failures = 0
            for _ in 0..<300 {
                if !bridge.start(port: port, token: "integration-test-token") { failures += 1 }
            }
            let rejected = !bridge.start(port: other, token: "new-token")
            let preserved = bridge.isListening && Int(bridge.port) == port
            for _ in 0..<100 {
                bridge.stop()
                if !bridge.start(port: port, token: "integration-test-token") { failures += 1 }
            }
            bridge.stop(); occupied.stop()
            try output(["failures": failures, "occupied_rejected": rejected, "old_listener_preserved": preserved])
            return
        }
        if args[1].hasPrefix("serve") {
            let bridge = AgentBridge()
            bridge.stories = store
            if args[1] == "serve-slow" || args[1] == "serve-fail" {
                bridge.fetchHandler = {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    if args[1] == "serve-fail" { throw StoryStore.Failure.database }
                    return 7
                }
            }
            guard bridge.start(port: Int(args[3])!, token: "integration-test-token") else { throw StoryStore.Failure.database }
            FileHandle.standardOutput.write(Data("ready\n".utf8))
            while !Task.isCancelled { try await Task.sleep(nanoseconds: 1_000_000_000) }
            return
        }
        if args[1] == "story" { try output(["story": store.story(args[3]) ?? [:]]); return }
        if args[1] == "revisions" { try output(["revisions": store.revisions(args[3])]); return }
        if args[1] == "pending" { try output(store.pending(consumer: args[3])); return }
        if args[1] == "ack" { try output(store.acknowledge(consumer: args[3], through: Int(args[4])!)); return }
    }
    static func output(_ value: Any) throws {
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
