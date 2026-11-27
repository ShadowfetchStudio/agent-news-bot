import Foundation

enum Desk: String, CaseIterable, Identifiable, Codable, Hashable {
    case breaking
    case politics
    case world
    case business
    case markets
    case tech
    case ai
    case science
    case health
    case entertainment
    case sports
    case hackerNews
    case polymarket
    case weather

    var id: String { rawValue }

    var title: String {
        switch self {
        case .breaking: return "Breaking"
        case .politics: return "Politics"
        case .world: return "World"
        case .business: return "Business"
        case .markets: return "Markets"
        case .tech: return "Tech"
        case .ai: return "AI"
        case .science: return "Science"
        case .health: return "Health"
        case .entertainment: return "Entertainment"
        case .sports: return "Sports"
        case .hackerNews: return "Hacker News"
        case .polymarket: return "Polymarket"
        case .weather: return "Weather"
        }
    }

    var systemImage: String {
        switch self {
        case .breaking: return "bolt.fill"
        case .politics: return "building.columns.fill"
        case .world: return "globe.americas.fill"
        case .business: return "briefcase.fill"
        case .markets: return "chart.line.uptrend.xyaxis"
        case .tech: return "laptopcomputer"
        case .ai: return "cpu.fill"
        case .science: return "atom"
        case .health: return "heart.fill"
        case .entertainment: return "theatermasks.fill"
        case .sports: return "sportscourt.fill"
        case .hackerNews: return "y.circle.fill"
        case .polymarket: return "chart.bar.doc.horizontal.fill"
        case .weather: return "cloud.sun.fill"
        }
    }

    var hashtags: [String] {
        switch self {
        case .breaking: return ["#Breaking", "#News"]
        case .politics: return ["#Politics"]
        case .world: return ["#WorldNews"]
        case .business: return ["#Business"]
        case .markets: return ["#Markets", "#Stocks"]
        case .tech: return ["#Technology"]
        case .ai: return ["#AI", "#ArtificialIntelligence"]
        case .science: return ["#Science"]
        case .health: return ["#Health"]
        case .entertainment: return ["#Entertainment"]
        case .sports: return ["#Sports"]
        case .hackerNews: return ["#HackerNews"]
        case .polymarket: return ["#Polymarket", "#PredictionMarkets"]
        case .weather: return ["#Weather"]
        }
    }
}

struct Article: Identifiable, Hashable, Codable {
    var id: String
    var desk: Desk
    var title: String
    var url: String
    var source: String
    var published: Date?
    var summary: String
    var tweet: String
    var tweetChars: Int
    var hashtags: [String]
    var extra: [String: String]
    var storyJSON: String? = nil

    var wire: [String: Any] {
        if let storyJSON, let data = storyJSON.data(using: .utf8),
           let story = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { return story }
        var details: [String: Any] = extra
        if let raw = extra["metadata"], let data = raw.data(using: .utf8),
           let metadata = try? JSONSerialization.jsonObject(with: data) { details["metadata"] = metadata }
        return ["id": id, "desk": desk.rawValue, "category": desk.title, "title": title,
            "url": url, "source": source, "published": published.map { ISO8601DateFormatter().string(from: $0) } ?? "",
            "summary": summary, "tweet": tweet, "tweet_chars": tweetChars, "hashtags": hashtags, "extra": details]
    }

    static func fromWire(_ value: [String: Any]) throws -> Article {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        var details: [String: String] = [:]
        for (key, item) in value["extra"] as? [String: Any] ?? [:] {
            if let text = item as? String { details[key] = text }
            else if JSONSerialization.isValidJSONObject(item) {
                details[key] = String(decoding: try JSONSerialization.data(withJSONObject: item, options: [.sortedKeys]), as: UTF8.self)
            }
        }
        return Article(id: value["id"] as? String ?? "", desk: Desk(rawValue: value["desk"] as? String ?? "") ?? .world,
            title: value["title"] as? String ?? "", url: value["url"] as? String ?? "", source: value["source"] as? String ?? "",
            published: DateStamp.parse(value["published"] as? String ?? ""), summary: value["summary"] as? String ?? "",
            tweet: value["tweet"] as? String ?? "", tweetChars: value["tweet_chars"] as? Int ?? 0,
            hashtags: value["hashtags"] as? [String] ?? [], extra: details, storyJSON: String(decoding: data, as: UTF8.self))
    }

    var desks: [Desk] {
        let values = (wire["desks"] as? [String] ?? [desk.rawValue]).compactMap(Desk.init(rawValue:))
        return values.isEmpty ? [desk] : values
    }

    var host: String {
        URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? ""
    }
}

struct FeedCacheUpdate {
    var url: String
    var etag: String?
    var lastModified: String?
    var body: Data? = nil
    var observedAt: Date? = nil
}

struct DeskResult: Identifiable {
    var id: Desk { desk }
    var desk: Desk
    var articles: [Article]
    var skippedOld: Int
    var skippedDup: Int
    var sourceErrors: [String]
    var cacheUpdates: [FeedCacheUpdate] = []
    var quoteUpdates: [(String, String)] = []
    var sourceChecks: [SourceCheck] = []
}

struct FetchReport {
    var started: Date
    var finished: Date
    var freshCount: Int
    var scanned: Int
    var desks: [DeskResult]
}

enum AgentKind: String, Codable {
    case hermes
    case openclaw
}

struct AgentStatus: Equatable {
    var kind: AgentKind
    var label: String
    var detail: String
    var ready: Bool
}

enum UserAgent {
    static let current =
        "AgentNewsBot/1.1 (+https://github.com/ShadowfetchStudio/agent-news-bot; Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
}

struct ConditionalResponse {
    var notModified: Bool
    var data: Data
    var etag: String?
    var lastModified: String?
}

enum Net {
    static func data(from url: URL, timeout: TimeInterval = 14, accept: String = "*/*") async throws -> Data {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.setValue(UserAgent.current, forHTTPHeaderField: "User-Agent")
        request.setValue(accept, forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return data
    }

    static func dataConditional(
        from url: URL,
        etag: String?,
        lastModified: String?,
        timeout: TimeInterval = 14,
        accept: String = "*/*"
    ) async throws -> ConditionalResponse {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.setValue(UserAgent.current, forHTTPHeaderField: "User-Agent")
        request.setValue(accept, forHTTPHeaderField: "Accept")
        if let etag, !etag.isEmpty {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        if let lastModified, !lastModified.isEmpty {
            request.setValue(lastModified, forHTTPHeaderField: "If-Modified-Since")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if http.statusCode == 304 {
            return ConditionalResponse(
                notModified: true,
                data: Data(),
                etag: http.value(forHTTPHeaderField: "ETag") ?? etag,
                lastModified: http.value(forHTTPHeaderField: "Last-Modified") ?? lastModified
            )
        }
        guard (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return ConditionalResponse(
            notModified: false,
            data: data,
            etag: http.value(forHTTPHeaderField: "ETag"),
            lastModified: http.value(forHTTPHeaderField: "Last-Modified")
        )
    }

    static func json(from url: URL, timeout: TimeInterval = 14) async throws -> Any {
        let data = try await data(from: url, timeout: timeout, accept: "application/json")
        return try JSONSerialization.jsonObject(with: data)
    }
}

enum HTMLText {
    static func strip(_ raw: String) -> String {
        var text = raw
        text = text.replacingOccurrences(of: "<br\\s*/?>", with: "\n", options: [.regularExpression, .caseInsensitive])
        text = text.replacingOccurrences(of: "</p>", with: "\n", options: [.regularExpression, .caseInsensitive])
        text = text.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
        let entities: [(String, String)] = [
            ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""),
            ("&#39;", "'"), ("&apos;", "'"), ("&nbsp;", " "), ("&#x27;", "'")
        ]
        for (from, to) in entities { text = text.replacingOccurrences(of: from, with: to) }
        text = text.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

enum CanonicalURL {
    /// Identity v2. Query order, duplicate keys, flags, plus signs and path slashes are significant.
    static func fingerprint(_ input: String) -> String {
        let raw = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: raw), let scheme = components.scheme?.lowercased(),
            ["http", "https"].contains(scheme), var host = components.url?.host?.lowercased(), !host.isEmpty else { return raw }
        if host.contains(":") && !host.hasPrefix("[") { host = "[" + host + "]" }
        if let port = components.port, !((scheme == "http" && port == 80) || (scheme == "https" && port == 443)) { host += ":\(port)" }
        func uppercaseEscapes(_ value: String) -> String {
            let regex = try! NSRegularExpression(pattern: "%[0-9a-fA-F]{2}")
            var result = value
            for match in regex.matches(in: value, range: NSRange(value.startIndex..., in: value)).reversed() {
                if let range = Range(match.range, in: result) { result.replaceSubrange(range, with: String(result[range]).uppercased()) }
            }
            return result
        }
        let path = components.percentEncodedPath == "/" ? "" : uppercaseEscapes(components.percentEncodedPath)
        let fields = (components.percentEncodedQuery ?? "").split(separator: "&", omittingEmptySubsequences: false).filter { field in
            let rawName = field.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? ""
            let name = (rawName.removingPercentEncoding ?? rawName).lowercased()
            return !(name.hasPrefix("utm_") || ["fbclid", "gclid", "ocid"].contains(name))
        }.map { uppercaseEscapes(String($0)) }
        let query = fields.joined(separator: "&")
        return scheme + "://" + host + path + (query.isEmpty ? "" : "?" + query)
    }
}

enum DateStamp {
    static func parse(_ raw: String?) -> Date? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: raw) { return date }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: raw) { return date }
        let rfc = DateFormatter()
        rfc.locale = Locale(identifier: "en_US_POSIX")
        rfc.timeZone = TimeZone(secondsFromGMT: 0)
        for format in [
            "EEE, dd MMM yyyy HH:mm:ss Z",
            "EEE, dd MMM yyyy HH:mm:ss zzz",
            "yyyy-MM-dd HH:mm:ss Z",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd"
        ] {
            rfc.dateFormat = format
            if let date = rfc.date(from: raw) { return date }
        }
        return nil
    }

    static func age(_ date: Date?) -> String {
        guard let date else { return "undated" }
        let minutes = Int(Date().timeIntervalSince(date) / 60)
        if minutes < 1 { return "just now" }
        if minutes < 60 { return "\(minutes)m ago" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours)h ago" }
        return "\(hours / 24)d ago"
    }
}
