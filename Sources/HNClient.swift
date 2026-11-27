import Foundation

enum HNClient {
    static func fetch(lookbackHours: Double, maxItems: Int, store: FreshnessStore) async -> DeskResult {
        var errors: [String] = []
        var checks: [SourceCheck] = []
        var skippedOld = 0
        let skippedDup = 0
        var articles: [Article] = []

        do {
            let newest = try await ids(from: "https://hacker-news.firebaseio.com/v0/newstories.json")
            let top = try await ids(from: "https://hacker-news.firebaseio.com/v0/topstories.json")
            checks.append(SourceCheck(source: "Hacker News indexes"))
            var ordered: [Int] = []
            var seen = Set<Int>()
            for id in newest.prefix(80) + top.prefix(40) where seen.insert(id).inserted {
                ordered.append(id)
            }

            let cutoff = Date().addingTimeInterval(-lookbackHours * 3600)
            for id in ordered {
                if articles.count >= maxItems { break }
                let candidate: Story?
                do { candidate = try await story(id) }
                catch { errors.append("Hacker News: item request failed"); continue }
                guard let item = candidate else { continue }
                guard item.type == "story" || item.type == nil else { continue }
                guard !item.title.isEmpty else { continue }
                let published = Date(timeIntervalSince1970: TimeInterval(item.time ?? 0))
                if published < cutoff {
                    skippedOld += 1
                    continue
                }
                let url = item.url?.isEmpty == false ? item.url! : "https://news.ycombinator.com/item?id=\(id)"
                guard ArticleURL.validated(url) != nil else { errors.append("Hacker News: skipped invalid article link"); continue }
                if url.contains("news.google.com") { continue }
                let fingerprint = FreshnessStore.articleID(url: CanonicalURL.fingerprint(url), fallback: "hn-\(id)")
                let extra = ["hn": "https://news.ycombinator.com/item?id=\(id)", "score": "\(item.score ?? 0)"]
                let tags = Hashtagger.tags(desk: .hackerNews, title: item.title, extra: item.score ?? 0 >= 100 ? ["#Trending"] : [])
                let (tweet, chars) = Hashtagger.tweet(title: item.title, url: url, tags: tags)
                articles.append(Article(
                    id: fingerprint,
                    desk: .hackerNews,
                    title: item.title,
                    url: CanonicalURL.fingerprint(url),
                    source: "HN · \(item.by ?? "anon") · \(item.score ?? 0)",
                    published: published,
                    summary: item.text.map(HTMLText.strip) ?? "",
                    tweet: tweet,
                    tweetChars: chars,
                    hashtags: tags,
                    extra: extra
                ))
            }
        } catch {
            errors.append("Hacker News: API unreachable")
        }

        articles.sort { ($0.published ?? .distantPast) > ($1.published ?? .distantPast) }
        return DeskResult(desk: .hackerNews, articles: articles, skippedOld: skippedOld, skippedDup: skippedDup, sourceErrors: errors, sourceChecks: checks)
    }

    private static func ids(from url: String) async throws -> [Int] {
        let any = try await Net.json(from: URL(string: url)!)
        guard let ids = any as? [Int] else { throw URLError(.cannotParseResponse) }
        return ids
    }

    private struct Story: Decodable {
        var id: Int
        var title: String
        var url: String?
        var by: String?
        var time: Int?
        var score: Int?
        var type: String?
        var text: String?
        var dead: Bool?
        var deleted: Bool?
    }

    private static func story(_ id: Int) async throws -> Story? {
        guard let url = URL(string: "https://hacker-news.firebaseio.com/v0/item/\(id).json") else { return nil }
        do {
            let data = try await Net.data(from: url, accept: "application/json")
            let item = try JSONDecoder().decode(Story.self, from: data)
            if item.dead == true || item.deleted == true { return nil }
            return item
        } catch {
            throw error
        }
    }
}
