import Foundation

enum Hashtagger {
    private static let skip: Set<String> = [
        "about", "after", "their", "there", "these", "those", "would", "could",
        "should", "might", "latest", "report", "reports", "update", "into",
        "from", "with", "that", "this", "have", "will", "your", "what", "when",
        "where", "which", "while", "over", "under", "more", "than", "been",
        "were", "they", "them", "just", "also", "says", "said"
    ]

    private static let known: [String: String] = [
        "ai": "AI", "llm": "LLM", "gpt": "GPT", "openai": "OpenAI",
        "nvidia": "NVIDIA", "apple": "Apple", "google": "Google",
        "microsoft": "Microsoft", "meta": "Meta", "tesla": "Tesla",
        "fed": "Fed", "sec": "SEC", "fcc": "FCC", "nasa": "NASA",
        "bbc": "BBC", "npr": "NPR", "cnn": "CNN", "nyc": "NYC",
        "usa": "USA", "uk": "UK", "eu": "EU", "un": "UN",
        "ipo": "IPO", "etf": "ETF", "btc": "BTC", "eth": "ETH",
        "spacex": "SpaceX"
    ]

    static func tags(desk: Desk, title: String, extra: [String] = []) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        func add(_ tag: String) {
            let key = tag.lowercased()
            guard !seen.contains(key) else { return }
            seen.insert(key)
            out.append(tag.hasPrefix("#") || tag.hasPrefix("$") ? tag : "#\(tag)")
        }
        desk.hashtags.forEach(add)
        extra.forEach(add)
        for token in keywords(in: title) where out.count < 6 {
            add(token)
        }
        return Array(out.prefix(6))
    }

    static func tweet(title: String, url: String, tags: [String]) -> (String, Int) {
        guard ArticleURL.validated(url) != nil else { return ("", 281) }
        // Fail closed: no copyable draft is safer than approving a post using an inaccurate fallback.
        do { return try XText.compose(title: title, url: url, tags: tags) }
        catch { return ("", 281) }
    }

    private static func keywords(in title: String) -> [String] {
        let words = title.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
        var found: [String] = []
        for word in words {
            let lower = word.lowercased()
            if let mapped = known[lower] {
                found.append("#\(mapped)")
                continue
            }
            if word.count >= 5, word.first?.isUppercase == true, !skip.contains(lower) {
                found.append("#\(word)")
            }
        }
        return found
    }
}
