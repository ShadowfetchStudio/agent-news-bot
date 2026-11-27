import Foundation

enum ResearchPackage {
    static func write(_ payload: [String: Any], in parent: URL) throws -> URL {
        try ArticleURL.requireSafeStory(payload["story"] as? [String: Any] ?? [:])
        guard let urls = payload["source_urls"] as? [String], urls.allSatisfy({ ArticleURL.validated($0) != nil }) else { throw URLError(.unsupportedURL) }
        let story = payload["story"] as? [String: Any] ?? [:], editorial = story["editorial"] as? [String: Any] ?? [:]
        let token = UUID().uuidString
        let dateStamp = {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            f.timeZone = TimeZone(secondsFromGMT: 0)
            return f.string(from: Date())
        }()
        let rawTitle = (story["title"] as? String ?? "story").lowercased()
        let slug = rawTitle.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).prefix(5).joined(separator: "-")
        let folderName = "Research-\(dateStamp)-\(slug.isEmpty ? "story" : String(slug.prefix(40)))-\(token.prefix(8))"
        let staging = parent.appendingPathComponent(".research-" + token), destination = parent.appendingPathComponent(folderName)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        do {
            let drafts = editorial["drafts"] as? [String: String] ?? [:], urls = payload["source_urls"] as? [String] ?? []
            var files = ["research.json": try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]),
                         "sources.txt": Data((urls.joined(separator: "\n") + "\n").utf8)]
            guard let brief = payload["brief"] as? [String: Any], let markdown = brief["markdown"] as? String else { throw LibraryFailure.invalid("Research brief is missing") }
            files["brief.md"] = Data(markdown.utf8)
            files["posts.md"] = Data((["# " + (story["title"] as? String ?? "Research"), "Editorial state: " + (editorial["status"] as? String ?? "unreviewed")] + LibraryModel.platforms.keys.sorted().map { name in "Platform approval: " + name + " — " + ((editorial["platforms"] as? [String: [String: Any]])?[name]?["status"] as? String ?? "unreviewed") } + LibraryModel.fields.keys.sorted().flatMap { ["\n## " + $0, drafts[$0] ?? ""] } + ["\n## Research notes", editorial["notes"] as? String ?? "", "\n## Sources", urls.joined(separator: "\n"), "\nMedia availability and reuse rights remain unknown. Review source terms before downloading, queueing, or publishing in any downstream workflow. No automatic downloading or posting was performed."]).joined(separator: "\n\n").utf8)
            for (name, data) in files { let file = staging.appendingPathComponent(name); try data.write(to: file, options: .atomic); try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path) }
            try FileManager.default.moveItem(at: staging, to: destination)
            return destination
        } catch { try? FileManager.default.removeItem(at: staging); throw error }
    }
}
