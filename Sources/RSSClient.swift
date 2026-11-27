import Foundation

enum RSSClient {
    static func fetch(desk: Desk, lookbackHours: Double, maxItems: Int, store: FreshnessStore, feeds: [FeedSource]? = nil) async -> DeskResult {
        var collected: [Article] = []
        var errors: [String] = []
        var skippedOld = 0
        var skippedDup = 0
        var seenURLs = Set<String>()
        var cacheUpdates: [FeedCacheUpdate] = []

        for feed in feeds ?? FeedRegistry.feeds(for: desk) {
            do {
                guard let url = URL(string: feed.url) else { continue }
                let cached = store.feedCache(feed.url)
                let document = store.feedDocument(feed.url)
                let res = try await Net.dataConditional(
                    from: url,
                    etag: document == nil ? nil : cached.etag,
                    lastModified: document == nil ? nil : cached.lastModified,
                    accept: "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
                )
                let body: Data, observedAt: Date
                if res.notModified {
                    guard let document else { throw URLError(.cannotParseResponse) }
                    body = document.data; observedAt = document.observedAt
                } else {
                    body = res.data
                    observedAt = document?.data == body ? document!.observedAt : Date()
                }
                guard body.count <= 4 * 1024 * 1024 else { throw URLError(.dataLengthExceedsMaximum) }
                let items = try RSSParser.parseChecked(data: body)
                if !res.notModified {
                    cacheUpdates.append(FeedCacheUpdate(url: feed.url, etag: res.etag, lastModified: res.lastModified, body: body, observedAt: observedAt))
                }
                for item in items {
                    let link = CanonicalURL.fingerprint(item.link)
                    guard !link.isEmpty, !link.contains("news.google.com") else { continue }
                    if seenURLs.contains(link) {
                        skippedDup += 1
                        continue
                    }
                    seenURLs.insert(link)
                    let published = DateStamp.parse(item.date)
                    if !store.isWithinLookback(published ?? observedAt, hours: lookbackHours) {
                        skippedOld += 1
                        continue
                    }
                    let id = FreshnessStore.articleID(url: link, fallback: item.title)
                    let title = HTMLText.strip(item.title)
                    guard !title.isEmpty else { continue }
                    let summary = HTMLText.strip(item.summary)
                    let tags = Hashtagger.tags(desk: desk, title: title)
                    let (tweet, chars) = Hashtagger.tweet(title: title, url: link, tags: tags)
                    collected.append(Article(
                        id: id,
                        desk: desk,
                        title: title,
                        url: link,
                        source: feed.label,
                        published: published,
                        summary: summary,
                        tweet: tweet,
                        tweetChars: chars,
                        hashtags: tags,
                        extra: ["observed_at": ISO8601DateFormatter().string(from: observedAt)]
                    ))
                }
            } catch {
                errors.append("\(feed.label): feed unreachable")
            }
            try? await Task.sleep(nanoseconds: 120_000_000)
        }

        collected.sort { ($0.published ?? .distantPast) > ($1.published ?? .distantPast) }
        if collected.count > maxItems { collected = Array(collected.prefix(maxItems)) }
        return DeskResult(desk: desk, articles: collected, skippedOld: skippedOld, skippedDup: skippedDup, sourceErrors: errors, cacheUpdates: cacheUpdates)
    }
}

struct RSSItem {
    var title = ""
    var link = ""
    var summary = ""
    var date = ""
}

final class RSSParser: NSObject, XMLParserDelegate {
    private var items: [RSSItem] = []
    private var current = RSSItem()
    private var inItem = false
    private var depth = 0
    private var itemDepth = 0
    private var field: String?
    private var fieldDepth = 0
    private var text = ""
    private var validRoot = false

    static func parse(data: Data) -> [RSSItem] { (try? parseChecked(data: data)) ?? [] }

    static func parseChecked(data: Data) throws -> [RSSItem] {
        let parser = RSSParser(), xml = XMLParser(data: data)
        xml.delegate = parser
        xml.shouldResolveExternalEntities = false
        guard xml.parse(), parser.validRoot else { throw URLError(.cannotParseResponse) }
        return parser.items
    }

    private func name(_ value: String) -> String { value.lowercased().split(separator: ":").last.map(String.init) ?? "" }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?, attributes attributes: [String: String] = [:]) {
        let tag = name(elementName)
        if depth == 0 { validRoot = ["rss", "feed", "rdf"].contains(tag) }
        depth += 1
        if depth > 64 { parser.abortParsing(); return }
        if !inItem && (tag == "item" || tag == "entry") {
            inItem = true; itemDepth = depth; current = RSSItem(); field = nil
        }
        guard inItem else { return }
        if depth == itemDepth + 1, tag == "link", let href = attributes["href"],
            ["", "alternate"].contains(attributes["rel"] ?? ""),
            ["", "text/html", "application/xhtml+xml"].contains(attributes["type"] ?? "") {
            current.link = href
        }
        if field == nil && depth == itemDepth + 1 && ["title", "link", "guid", "id", "description", "summary", "content", "encoded", "pubdate", "published", "updated", "date"].contains(tag) {
            field = tag; fieldDepth = depth; text = ""
        }
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) {
        if field != nil && text.count < 65536 { text += String(string.prefix(65536 - text.count)) }
    }
    func parser(_ parser: XMLParser, foundCDATA data: Data) {
        if let string = String(data: data, encoding: .utf8) { self.parser(parser, foundCharacters: string) }
    }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        let tag = name(elementName)
        if let field, depth == fieldDepth {
            let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            switch field {
            case "title": current.title = value
            case "link": if current.link.isEmpty { current.link = value }
            case "guid", "id": if current.link.isEmpty && value.hasPrefix("http") { current.link = value }
            case "description", "summary", "content", "encoded": if current.summary.count < value.count { current.summary = value }
            case "pubdate", "published", "updated", "date": if current.date.isEmpty { current.date = value }
            default: break
            }
            self.field = nil; text = ""
        } else if field != nil && ["p", "div", "br"].contains(tag) { text += " " }
        if depth == itemDepth && (tag == "item" || tag == "entry") {
            if !current.title.isEmpty { items.append(current) }
            inItem = false; field = nil
        }
        depth -= 1
    }
}
