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
                detail: settings.openClawURL,
                ready: true
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
        try await Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: path)
            process.arguments = ["agent", "--message", prompt]
            let out = Pipe()
            let err = Pipe()
            process.standardOutput = out
            process.standardError = err
            try process.run()

            var stdoutData = Data()
            var stderrData = Data()
            let group = DispatchGroup()
            group.enter()
            DispatchQueue.global().async {
                stdoutData = out.fileHandleForReading.readDataToEndOfFile()
                group.leave()
            }
            group.enter()
            DispatchQueue.global().async {
                stderrData = err.fileHandleForReading.readDataToEndOfFile()
                group.leave()
            }
            group.wait()
            process.waitUntilExit()

            let stdout = String(data: stdoutData, encoding: .utf8) ?? ""
            if process.terminationStatus != 0 {
                let stderr = String(data: stderrData, encoding: .utf8) ?? ""
                throw NSError(domain: "OpenClaw", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: stderr.isEmpty ? stdout : stderr])
            }
            return stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        }.value
    }

    static func portOpen(_ urlString: String) async -> Bool {
        guard let url = URL(string: urlString), let host = url.host else { return false }
        let port = url.port ?? (url.scheme == "wss" || url.scheme == "https" ? 443 : 18789)
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(port)) else { return false }
        return await withCheckedContinuation { cont in
            let lock = NSLock()
            var done = false
            let finish: @Sendable (Bool, NWConnection) -> Void = { value, connection in
                lock.lock()
                defer { lock.unlock() }
                guard !done else { return }
                done = true
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
