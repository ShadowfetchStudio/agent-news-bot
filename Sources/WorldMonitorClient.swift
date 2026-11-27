import Foundation

private final class NoWorldMonitorRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

enum WorldMonitorClient {
    static let variants = ["full", "tech", "finance", "happy", "commodity", "energy"]
    enum Failure: Error { case configuration, payload, oversized, http(Int) }

    struct Diagnostic {
        let code: String
        let message: String
        let succeeded: Bool
    }

    static func diagnosis(_ error: Error) -> Diagnostic {
        var code = "network", message = "Cannot reach World Monitor. Check the server, network and port."
        switch error {
        case Failure.configuration:
            code = "configuration"; message = "Use an HTTPS server origin, or HTTP on localhost, with no path, query or credentials. Check the edition and API key format."
        case Failure.payload:
            code = "schema"; message = "The server did not return a compatible news digest. Check its API version and origin."
        case Failure.oversized:
            code = "response_too_large"; message = "The digest exceeds the 4 MiB limit. Choose a narrower edition or contact the provider."
        case Failure.http(let status):
            code = "http_\(status)"
            switch status {
            case 401: message = "World Monitor HTTP 401: authentication failed. Check the API key."
            case 403: message = "World Monitor HTTP 403: access denied. Check your account or server permissions."
            case 404: message = "World Monitor HTTP 404: digest endpoint not found. Check the server origin and API compatibility."
            case 429: message = "World Monitor HTTP 429: rate limited. Wait before retrying and check the provider's quota."
            case 300...399: message = "World Monitor HTTP \(status): redirect refused to protect your key. Enter the final authorized server origin."
            case 500...599: message = "World Monitor HTTP \(status): provider unavailable. Retry later; direct sources remain independent."
            default: message = "World Monitor HTTP \(status): request rejected. Check the provider configuration."
            }
        case let url as URLError:
            if url.code == .timedOut { code = "timeout"; message = "World Monitor exceeded its request deadline. Check the server and retry later." }
            else if [-1200, -1201, -1202, -1203, -1204, -1205, -1206].contains(url.errorCode) {
                code = "tls"; message = "World Monitor TLS validation failed. Fix the server certificate; verification remains enabled."
            } else if url.code == .cancelled { code = "cancelled"; message = "Connection test cancelled." }
        default: break
        }
        return Diagnostic(code: code, message: message, succeeded: false)
    }

    static func test(base: String, key: String, variant: String) async -> Diagnostic {
        do {
            let results = try await retrieve(base: base, key: key, variant: variant, lookback: 12, limit: 20)
            let state = Coverage.summary(results.flatMap(\.coverageChecks))["state"] as? String ?? "unknown"
            return Diagnostic(code: "connected", message: "Compatible digest received. Coverage: \(state). This checks access and format, not factual accuracy or content rights. No stories saved.", succeeded: true)
        } catch { return diagnosis(error) }
    }

    static func origin(_ raw: String) throws -> URL {
        guard let c = URLComponents(string: raw), let host = c.host, !host.isEmpty else { throw Failure.configuration }
        let scheme = c.scheme?.lowercased() ?? ""
        let lowerHost = host.lowercased()
        guard ArticleURL.validated(raw) != nil,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.port == nil || (1...65535).contains(c.port!),
              c.path == "" || c.path == "/", ["http", "https"].contains(scheme),
              scheme == "https" || ["localhost", "127.0.0.1", "::1", "[::1]"].contains(lowerHost),
              let url = c.url else { throw Failure.configuration }
        return url
    }

    static func fetch(enabled: Bool, base: String, key: String, variant: String, lookback: Double, limit: Int) async -> [DeskResult] {
        guard enabled else { return [] }
        return await SourceHealth.shared.run(key:"worldmonitor:" + base + ":" + variant,source:"World Monitor digest",desk:.world,provider:"worldmonitor") {
            await collect(base:base,key:key,variant:variant,lookback:lookback,limit:limit)
        }
    }

    private static func collect(base: String, key: String, variant: String, lookback: Double, limit: Int) async -> [DeskResult] {
        do { return try await retrieve(base: base, key: key, variant: variant, lookback: lookback, limit: limit) }
        catch {
            let failure = diagnosis(error)
            return [DeskResult(desk: .world, articles: [], skippedOld: 0, skippedDup: 0,
                sourceErrors: [failure.message], sourceChecks: [SourceCheck(source: "World Monitor digest",
                provider: "worldmonitor", state: "unavailable", detail: failure.message)])]
        }
    }

    private static func retrieve(base: String, key: String, variant: String, lookback: Double, limit: Int) async throws -> [DeskResult] {
        guard base.utf8.count <= 4096, key.utf8.count <= 8192,
              key.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value <= 126 }),
              variants.contains(variant) else { throw Failure.configuration }
        let root = try origin(base)
        var c = URLComponents(url: root.appendingPathComponent("api/news/v1/list-feed-digest"), resolvingAgainstBaseURL: false)!
        c.queryItems = [URLQueryItem(name: "variant", value: variant), URLQueryItem(name: "lang", value: "en")]
        var request = URLRequest(url: c.url!)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !key.isEmpty { request.setValue(key, forHTTPHeaderField: "X-WorldMonitor-Key") }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        let session = URLSession(configuration: config, delegate: NoWorldMonitorRedirects(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw Failure.payload }
        guard http.statusCode == 200 else {
            SourceAttempt.current?.record("http_\(http.statusCode)",retryAfter:http.value(forHTTPHeaderField:"Retry-After"))
            throw Failure.http(http.statusCode)
        }
        var data = Data()
        for try await byte in bytes {
            data.append(byte)
            if data.count > 4 * 1024 * 1024 { throw Failure.oversized }
        }
        do { return try parse(data: data, lookback: lookback, limit: limit) }
        catch { throw Failure.payload }
    }

    static func parse(data: Data, lookback: Double, limit: Int, now: Date = Date()) throws -> [DeskResult] {
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let categories = payload["categories"] as? [String: Any] else { throw Failure.payload }
        var results: [Desk: DeskResult] = [:]
        var skipped = 0
        for category in categories.keys.sorted() {
            guard let bucket = categories[category] as? [String: Any], let rows = bucket["items"] as? [Any] else { throw Failure.payload }
            let desk = Desk(rawValue: category) ?? (["technology": Desk.tech, "finance": Desk.business][category] ?? .world)
            var result = results[desk] ?? DeskResult(desk: desk, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: [])
            for raw in rows.prefix(1000) {
                guard let row = raw as? [String: Any], let rawLink = row["link"] as? String,
                      ArticleURL.validated(rawLink) != nil,
                      let c = URLComponents(string: rawLink), ["http", "https"].contains(c.scheme?.lowercased() ?? ""), c.host != nil,
                      let rawTitle = row["title"] as? String, let millis = row["publishedAt"] as? NSNumber,
                      millis.doubleValue.isFinite else { skipped += 1; continue }
                let title = String(HTMLText.strip(rawTitle).prefix(2000))
                let publisher = ["publisherName", "originPublisher", "source"].compactMap { row[$0] as? String }.first { !$0.isEmpty } ?? ""
                let source = String(HTMLText.strip(publisher).prefix(200))
                guard !title.isEmpty, !source.isEmpty else { skipped += 1; continue }
                let published = Date(timeIntervalSince1970: millis.doubleValue / 1000)
                let age = now.timeIntervalSince(published)
                if age > lookback * 3600 || age < -300 { result.skippedOld += 1; continue }
                let link = CanonicalURL.fingerprint(rawLink)
                var metadata: [String: Any] = [:]
                for k in ["location", "locationName", "threat", "importanceScore", "credibilityScore", "credibilityUnassessed", "corroborationCount", "storyMeta", "tickers", "linkVerified"] { metadata[k] = row[k] }
                metadata["category"] = category
                metadata["generated_at"] = payload["generatedAt"] ?? ""
                metadata["coverage"] = payload["coverage"] ?? [:]
                let metadataData = try JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys])
                let tags = Hashtagger.tags(desk: desk, title: title)
                let (tweet, chars) = Hashtagger.tweet(title: title, url: link, tags: tags)
                result.articles.append(Article(id: FreshnessStore.articleID(url: link, fallback: title), desk: desk,
                    title: title, url: link, source: source, published: published,
                    summary: String(HTMLText.strip(row["snippet"] as? String ?? "").prefix(2000)), tweet: tweet,
                    tweetChars: chars, hashtags: tags, extra: ["provider": "worldmonitor", "metadata": String(decoding: metadataData, as: UTF8.self)]))
            }
            results[desk] = result
        }
        for desk in Array(results.keys) {
            results[desk]!.articles.sort { ($0.published ?? .distantPast) > ($1.published ?? .distantPast) }
            results[desk]!.articles = Array(results[desk]!.articles.prefix(limit))
        }
        var warnings: [String] = []
        if let coverage = payload["coverage"] as? [String: Any], let state = coverage["state"] as? String,
           ["partial", "stale", "unavailable"].contains(state) { warnings.append("World Monitor coverage: \(state)") }
        if let statuses = payload["feedStatuses"] as? [String: Any], !statuses.isEmpty { warnings.append("World Monitor reports upstream feed gaps") }
        if skipped > 0 { warnings.append("World Monitor skipped \(skipped) invalid or undated items") }
        if !warnings.isEmpty {
            var result = results[.world] ?? DeskResult(desk: .world, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: [])
            result.sourceErrors += warnings
            results[.world] = result
        }
        let reported = (payload["coverage"] as? [String: Any])?["state"] as? String ?? "complete"
        var state = reported == "complete" ? "success" : reported
        if !["success", "partial", "stale", "unavailable"].contains(state) { state = "partial" }
        if state == "success" && !warnings.isEmpty { state = "partial" }
        if results.isEmpty { results[.world] = DeskResult(desk: .world, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: []) }
        for desk in Array(results.keys) {
            results[desk]!.sourceChecks = [SourceCheck(source: "World Monitor digest", provider: "worldmonitor", state: state, detail: warnings.joined(separator: "; "))]
        }
        return Array(results.values)
    }
}
