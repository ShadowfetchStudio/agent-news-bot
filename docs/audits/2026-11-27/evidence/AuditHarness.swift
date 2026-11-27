import Foundation

@main struct AuditHarness {
    static func main() async throws {
        let args = CommandLine.arguments
        if args[1] == "canonical" {
            print(CanonicalURL.fingerprint(args[2])); return
        }
        if args[1] == "restart" {
            let bridge = AgentBridge()
            let port = Int(args[2])!
            var failures = 0
            for _ in 0..<30 {
                if !bridge.start(port: port, token: "audit-token") { failures += 1 }
            }
            print("restart failures: \(failures)/30")
            bridge.stop(); return
        }
        let bridge = AgentBridge()
        bridge.stories = StoryStore(path: URL(fileURLWithPath: args[2]))
        if args[1] == "timeout" {
            bridge.fetchHandler = { try? await Task.sleep(nanoseconds: 65_000_000_000); return 7 }
        }
        guard bridge.start(port: Int(args[3])!, token: "audit-token") else { print("bind failed"); return }
        FileHandle.standardOutput.write(Data("ready\n".utf8))
        while true { try await Task.sleep(nanoseconds: 1_000_000_000) }
    }
}
