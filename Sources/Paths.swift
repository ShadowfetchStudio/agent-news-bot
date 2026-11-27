import Foundation

enum AppPaths {
    static let appName = "Agent News Bot"
    static var bundleId: String {
        if let home = ProcessInfo.processInfo.environment["AGENT_NEWSBOT_HOME"], !home.isEmpty {
            // Isolated app runs must not share user preferences or Keychain accounts with production.
            return "ai.shadowfetch.agentnewsbot.test." + String(home.utf8.reduce(UInt64(14695981039346656037)) { ($0 ^ UInt64($1)) &* 1099511628211 }, radix: 16)
        }
        return "ai.shadowfetch.agentnewsbot"
    }

    static var support: URL {
        let url: URL
        if let home = ProcessInfo.processInfo.environment["AGENT_NEWSBOT_HOME"], !home.isEmpty {
            url = URL(fileURLWithPath: home, isDirectory: true)
        } else {
            url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("AgentNewsBot", isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static var exports: URL {
        let url = support.appendingPathComponent("Exports", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static var seenDB: URL { support.appendingPathComponent("seen.sqlite") }
    static var tokenFile: URL { support.appendingPathComponent("bridge.token") }
}
