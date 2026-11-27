import Foundation
import JavaScriptCore

// JavaScriptCore is built into macOS. Only the pinned bundled parser is evaluated.
// The serial queue confines the context and all JSValue accesses to one executor.
enum XText {
    private static let queue = DispatchQueue(label: "ai.shadowfetch.newsbot.xtext")
    private static var context: JSContext?
    enum Failure: Error { case missingParser, invalidDraft }

    static func request(_ value: [String: Any]) throws -> [String: Any] {
        try queue.sync {
            if context == nil {
                let paths = [Bundle.main.resourceURL?.appendingPathComponent("twitter-text.js"),
                    URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent("twitter-text.js")]
                guard let path = paths.compactMap({ $0 }).first(where: { FileManager.default.fileExists(atPath: $0.path) }),
                      let source = try? String(contentsOf: path, encoding: .utf8), let js = JSContext() else { throw Failure.missingParser }
                js.evaluateScript(source)
                guard js.exception == nil else { throw Failure.missingParser }
                context = js
            }
            guard let js = context else { throw Failure.missingParser }
            js.exception = nil
            let json = String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
            let result = js.objectForKeyedSubscript("NewsBotXText")?.objectForKeyedSubscript("request")?.call(withArguments: [json])
            guard js.exception == nil, let output = result?.toString(), let data = output.data(using: .utf8),
                  let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw Failure.invalidDraft }
            return parsed
        }
    }

    static func compose(title: String, url: String, tags: [String]) throws -> (String, Int) {
        let value = try request(["op": "compose", "title": String(title.prefix(16384)), "url": String(url.prefix(8192)), "tags": tags.prefix(6).map { String($0.prefix(1024)) }])
        guard let text = value["text"] as? String, let count = value["weightedLength"] as? Int,
              text.isEmpty || value["valid"] as? Bool == true else { throw Failure.invalidDraft }
        return (text, count)
    }
}
