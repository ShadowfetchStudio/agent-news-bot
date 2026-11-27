import Foundation

enum ResearchPackage {
    static func write(_ payload: [String: Any], in parent: URL) throws -> URL {
        let token = UUID().uuidString, staging = parent.appendingPathComponent(".research-" + token), destination = parent.appendingPathComponent("Research-" + token)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        do {
            let story = payload["story"] as? [String: Any] ?? [:], editorial = story["editorial"] as? [String: Any] ?? [:]
            let drafts = editorial["drafts"] as? [String: String] ?? [:], urls = payload["source_urls"] as? [String] ?? []
            var files = ["research.json": try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]),
                         "sources.txt": Data((urls.joined(separator: "\n") + "\n").utf8)]
            files["posts.md"] = Data((["# " + (story["title"] as? String ?? "Research"), "Editorial state: " + (editorial["status"] as? String ?? "unreviewed")] + LibraryModel.fields.keys.sorted().flatMap { ["\n## " + $0, drafts[$0] ?? ""] } + ["\n## Research notes", editorial["notes"] as? String ?? "", "\n## Sources", urls.joined(separator: "\n"), "\nMedia rights remain unknown. Paste sources.txt into VideoBot Inspect URLs, review discovered media, then choose what to queue. No downloading or posting was performed."]).joined(separator: "\n\n").utf8)
            for (name, data) in files { let file = staging.appendingPathComponent(name); try data.write(to: file, options: .atomic); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path) }
            try FileManager.default.moveItem(at: staging, to: destination)
            return destination
        } catch { try? FileManager.default.removeItem(at: staging); throw error }
    }
}
