import Foundation
import Network

enum OpenClawClient {
    @MainActor
    static func probe(settings: SettingsStore) async -> AgentStatus {
        if let cli = HermesClient.which("openclaw") {
            let listening = await portOpen(settings.openClawURL)
            return AgentStatus(
                kind: .openclaw,
                label: "OpenClaw CLI",
                detail: listening ? "CLI + gateway \(settings.openClawURL)" : cli,
                ready: true
            )
        }
        if await portOpen(settings.openClawURL) {
            return AgentStatus(
                kind: .openclaw,
                label: "OpenClaw Gateway",
                detail: "Gateway detected; install CLI for handoff, or pull via the local bridge.",
                ready: false
            )
        }
        return AgentStatus(
            kind: .openclaw,
            label: "OpenClaw",
            detail: "Gateway not on \(settings.openClawURL). Agents can still pull via the local bridge.",
            ready: false
        )
    }

    @MainActor
    static func send(articles: [Article], settings: SettingsStore) async throws -> String {
        let body = HermesClient.brief(articles)
        if let path = HermesClient.which("openclaw") {
            return try await runCLI(path: path, prompt: body)
        }
        throw NSError(
            domain: "OpenClaw",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "OpenClaw CLI not installed. Start the gateway and have the agent GET the local bridge /v1/fresh instead."]
        )
    }

    private static func runCLI(path: String, prompt: String) async throws -> String {
        try await AgentProcess.run(path: path, arguments: ["agent", "--message", prompt])
    }

    static func portOpen(_ urlString: String) async -> Bool {
        guard let url = URL(string: urlString), let host = url.host else { return false }
        let port = url.port ?? (url.scheme == "wss" || url.scheme == "https" ? 443 : 18789)
        guard (1...65535).contains(port), let nwPort = NWEndpoint.Port(rawValue: UInt16(port)) else { return false }
        return await withCheckedContinuation { cont in
            let completion = ProbeCompletion()
            let finish: @Sendable (Bool, NWConnection) -> Void = { value, connection in
                guard completion.claim() else { return }
                connection.cancel()
                cont.resume(returning: value)
            }
            let connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .tcp)
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    finish(true, connection)
                case .failed, .cancelled:
                    finish(false, connection)
                default:
                    break
                }
            }
            connection.start(queue: .global())
            DispatchQueue.global().asyncAfter(deadline: .now() + 1.2) {
                finish(false, connection)
            }
        }
    }
}

private final class ProbeCompletion: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    func claim() -> Bool { lock.withLock { if done { return false }; done = true; return true } }
}
