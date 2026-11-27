import Foundation
import Security

@main struct IntegrationHarness {
    static func main() async throws {
        let args = CommandLine.arguments
        if args[1] == "health-network" {
            let health = SourceHealth(path: URL(fileURLWithPath:args[2]))
            let results = await health.run(key:"network-fixture",source:"Fixture",desk:.science) {
                var errors: [String] = []
                for _ in 0..<10 {
                    do { _ = try await Net.data(from:URL(string:args[3])!) }
                    catch { errors.append("request failed") }
                }
                return [DeskResult(desk:.science,articles:[],skippedOld:0,skippedDup:0,sourceErrors:errors)]
            }
            try output(["coverage":Coverage.summary(results.flatMap(\.coverageChecks)),"history":try health.list()]); return
        }
        if args[1] == "health" {
            let health = SourceHealth(path:URL(fileURLWithPath:args[2])), now = Double(args[4])!
            do {
                switch args[3] {
                case "begin":
                    let (lease,row) = try health.begin(key:args[5],source:"Fixture",now:now)
                    try output(["lease":lease as Any? ?? NSNull(),"row":row])
                case "finish":
                    try output(try health.finish(key:args[5],lease:args[6],state:args[7],code:args.count>8 ? args[8] : "",retryAt:args.count>9 ? Double(args[9])! : 0,now:now))
                case "retry": try output(["retry_at":SourceHealth.retryTime(args[5],now:now)])
                default: try output(["sources":try health.list(now:now)])
                }
            } catch { try output(["error":"health storage or request failure"]) }
            return
        }
        if args[1] == "setup-policy" {
            let values = await MainActor.run { () -> [String: Any] in
                let name = "ai.shadowfetch.agentnewsbot.setup-test." + UUID().uuidString
                let defaults = UserDefaults(suiteName: name)!
                defer { defaults.removePersistentDomain(forName: name) }
                let fresh = SettingsStore(defaults: defaults)
                let first = fresh.setupCompleted
                fresh.save()
                let pending = SettingsStore(defaults: defaults).setupCompleted
                fresh.setupCompleted = true; fresh.save()
                let completed = SettingsStore(defaults: defaults).setupCompleted
                defaults.removePersistentDomain(forName: name)
                defaults.set(false, forKey: "autoRefresh")
                defaults.set([String](), forKey: "enabledDesks")
                defaults.set("https://existing.example", forKey: "worldMonitorURL")
                let legacy = SettingsStore(defaults: defaults)
                return ["fresh": first, "pending": pending, "completed": completed,
                        "legacy": legacy.setupCompleted, "auto_refresh": legacy.autoRefresh,
                        "desks": legacy.enabledDesks.count, "origin": legacy.worldMonitorURL]
            }
            try output(values); return
        }
        if args[1] == "wm-test" {
            let result = await WorldMonitorClient.test(base: args[2], key: args.count > 4 ? args[4] : "fixture-secret", variant: args.count > 3 ? args[3] : "full")
            try output(["code": result.code, "message": result.message, "succeeded": result.succeeded]); return
        }
        if args[1] == "snapshot-save" {
            do {
                let directory = URL(fileURLWithPath: args[2])
                let mode = args.count > 3 ? args[3] : "ok"
                let value: [String: Any] = mode == "large" ? ["data": String(repeating: "x", count: 16 * 1024 * 1024)] : ["value": mode]
                let archive = try SnapshotWriter.save(value, in: directory, commit: { source, destination in
                    if mode == "full" { throw SnapshotWriter.Failure.io(ENOSPC) }
                    try SnapshotWriter.replace(source, destination)
                })
                try output(["archive": archive.path])
            } catch { try output(["error": error.localizedDescription]) }
            return
        }
        if args[1] == "http-get" {
            do { try output(["bytes": try await Net.data(from: URL(string: args[2])!, timeout: Double(args[3])!).count]) }
            catch { try output(["error": error.localizedDescription]) }
            return
        }
        if args[1] == "safe-urls" {
            let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [String]
            try output(["safe": rows.map { ArticleURL.validated($0) != nil }]); return
        }
        if args[1] == "credential" {
            do {
                let path = URL(fileURLWithPath: args[2])
                let token = args.count > 3 ? try BridgeCredential.rotate(at: path, expected: args[3]) : try BridgeCredential.loadOrCreate(at: path)
                try output(["token": token])
            } catch { try output(["error": error.localizedDescription]) }
            return
        }
        if args[1] == "credential-server" {
            let path = URL(fileURLWithPath: args[2]), bridge = AgentBridge()
            let old = try BridgeCredential.loadOrCreate(at: path)
            guard bridge.start(port: Int(args[3])!, token: old) else { throw StoryStore.Failure.database }
            print("ready"); fflush(stdout)
            _ = readLine()
            let updated = try BridgeCredential.rotate(at: path, expected: old)
            bridge.replaceToken(updated)
            print("rotated"); fflush(stdout)
            try await Task.sleep(nanoseconds: 30_000_000_000)
            return
        }
        if args[1] == "port-probe" { try output(["ready": await OpenClawClient.portOpen(args[2])]); return }
        if ["hermes-api", "hermes-probe", "hermes-test"].contains(args[1]) {
            let settings = await MainActor.run { () -> SettingsStore in
                let settings = SettingsStore.shared
                settings.hermesUseCLI = false
                settings.hermesAPIURL = args[2]
                if args.count > 3 { settings.hermesModel = args[3] }
                return settings
            }
            if args[1] == "hermes-probe" {
                let status = await HermesClient.probe(settings: settings)
                try output(["ready": status.ready, "can_send": status.canSend, "detail": status.detail]); return
            }
            if args[1] == "hermes-test" {
                do { try await HermesClient.test(settings: settings); try output(["tested": true]) }
                catch { try output(["error": error.localizedDescription]) }
                return
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
                enabled: [.science, .tech], limit: value["limit"] as? Int ?? 20, now: Date(timeIntervalSince1970: 1795780800)).map(\.wire)]); return
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
        if args[1] == "library-op" {
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[3]))) as! [String: Any]
            switch value["op"] as? String {
            case "alerts": try output(try store.alerts(value["query"] as? [String: String] ?? [:]))
            case "alert-change":
                do { try output(try store.alertChange(value["change"] as! [String: Any])) }
                catch LibraryFailure.conflict(let message) { try output(["error":"conflict","message":message]) }
                catch LibraryFailure.invalid(let message) { try output(["error":"invalid","message":message]) }
                catch LibraryFailure.missing { try output(["error":"missing"]) }
            case "repair":
                do { try output(try store.repairGroup(value["change"] as! [String: Any])) }
                catch LibraryFailure.conflict(let message) { try output(["error": "conflict", "message": message]) }
                catch LibraryFailure.invalid(let message) { try output(["error": "invalid", "message": message]) }
                catch LibraryFailure.missing { try output(["error": "missing"]) }
                catch { try output(["error": "storage"]) }
            case "repair-receipt": try output(try store.repairReceipt(value["id"] as! String) ?? [:])
            case "reconcile-groups": try output(["stories": store.reconcileGroups(value["stories"] as! [[String: Any]])])
            case "search": try output(try store.library(value["query"] as? [String: String] ?? [:]))
            case "save": try output(try store.saveEditorial(value["id"] as! String, change: value["change"] as! [String: Any]))
            case "settings": try output(try store.librarySettings(kind: value["kind"] as? String, name: value["name"] as? String, value: value["value"], write: value["write"] as? Bool ?? false))
            case "brief": try output(try store.researchBrief(value["id"] as! String))
            case "build-brief": try output(EditorialBrief.build(value["story"] as! [String: Any], previous: value["previous"] as? [String: Any]))
            case "export": try output(try store.researchExport(value["id"] as! String))
            case "export-files": try output(["folder": ResearchPackage.write(store.researchExport(value["id"] as! String), in: URL(fileURLWithPath: value["destination"] as! String)).path])
            default: try output(try store.editorial(value["id"] as! String) ?? [:])
            }
            return
        }
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
            bridge.sourceHealth = SourceHealth(path: URL(fileURLWithPath: args[2]).deletingLastPathComponent().appendingPathComponent("source-health.sqlite"))
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
