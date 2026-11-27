import Foundation

private final class NoWorldMonitorRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

enum WorldMonitorClient {
    static let variants = ["full", "tech", "finance", "happy", "commodity", "energy"]
    enum Failure: Error { case configuration, payload }

    static func origin(_ raw: String) throws -> URL {
        guard let c = URLComponents(string: raw), let host = c.host, !host.isEmpty,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.path == "" || c.path == "/", ["http", "https"].contains(c.scheme ?? ""),
              c.scheme == "https" || ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host),
              let url = c.url else { throw Failure.configuration }
        return url
    }

    static func fetch(enabled: Bool, base: String, key: String, variant: String, lookback: Double, limit: Int) async -> [DeskResult] {
        guard enabled else { return [] }
        do {
            let root = try origin(base)
            guard variants.contains(variant) else { throw Failure.configuration }
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
                return [DeskResult(desk: .world, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: ["World Monitor HTTP \(http.statusCode)"])]
            }
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count > 4 * 1024 * 1024 { throw Failure.payload }
            }
            return try parse(data: data, lookback: lookback, limit: limit)
        } catch {
            return [DeskResult(desk: .world, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: ["World Monitor unavailable or invalid response/configuration"])]
        }
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
                      let c = URLComponents(string: rawLink), ["http", "https"].contains(c.scheme ?? ""), c.host != nil,
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
        return Array(results.values)
    }
}
