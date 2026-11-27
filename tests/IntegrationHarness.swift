import Foundation

@main struct IntegrationHarness {
    static func main() async throws {
        let args = CommandLine.arguments
        if args[1] == "fetch" {
            let results = await WorldMonitorClient.fetch(enabled: true, base: args[2], key: "test-fixture-key", variant: "tech", lookback: 100000, limit: 20)
            try output(["articles": results.flatMap(\.articles).map(\.wire), "errors": results.flatMap(\.sourceErrors)])
            return
        }
        if args[1] == "digest" {
            let data = try Data(contentsOf: URL(fileURLWithPath: args[2]))
            let results = try WorldMonitorClient.parse(data: data, lookback: 12, limit: 20, now: Date(timeIntervalSince1970: 1795780800))
            try output(["articles": results.flatMap(\.articles).map(\.wire), "errors": results.flatMap(\.sourceErrors)])
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
        if args[1] == "serve" {
            let bridge = AgentBridge()
            bridge.stories = store
            guard bridge.start(port: Int(args[3])!, token: "integration-test-token") else { throw StoryStore.Failure.database }
            FileHandle.standardOutput.write(Data("ready\n".utf8))
            while !Task.isCancelled { try await Task.sleep(nanoseconds: 1_000_000_000) }
            return
        }
        if args[1] == "pending" { try output(store.pending(consumer: args[3])); return }
        if args[1] == "ack" { try output(store.acknowledge(consumer: args[3], through: Int(args[4])!)); return }
    }
    static func output(_ value: Any) throws {
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
