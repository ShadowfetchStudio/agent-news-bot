import Foundation

enum WeatherClient {
    static func fetch(city: String, store: FreshnessStore) async -> DeskResult {
        let results = await store.health.run(key: "direct:weather:" + city, source: "Open-Meteo", desk: .weather) {
            [await collect(city: city, store: store)]
        }
        return results[0]
    }

    private static func collect(city: String, store: FreshnessStore) async -> DeskResult {
        var errors: [String] = []
        let query = city.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else {
            return DeskResult(desk: .weather, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: ["Set a weather city in Settings"])
        }

        do {
            var geoParts = URLComponents(string: "https://geocoding-api.open-meteo.com/v1/search")!
            geoParts.queryItems = [
                URLQueryItem(name: "name", value: query),
                URLQueryItem(name: "count", value: "1"),
                URLQueryItem(name: "language", value: "en"),
                URLQueryItem(name: "format", value: "json")
            ]
            guard let geoURL = geoParts.url else { throw URLError(.badURL) }
            let geo = try await Net.json(from: geoURL)
            guard
                let results = (geo as? [String: Any])?["results"] as? [[String: Any]],
                let first = results.first,
                let lat = first["latitude"] as? Double,
                let lon = first["longitude"] as? Double
            else {
                return DeskResult(desk: .weather, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: ["Could not geocode \(query)"])
            }
            let name = (first["name"] as? String) ?? query
            let admin = (first["admin1"] as? String) ?? ""
            let country = (first["country"] as? String) ?? ""
            let place = [name, admin, country].filter { !$0.isEmpty }.joined(separator: ", ")

            var wxParts = URLComponents(string: "https://api.open-meteo.com/v1/forecast")!
            wxParts.queryItems = [
                URLQueryItem(name: "latitude", value: String(lat)),
                URLQueryItem(name: "longitude", value: String(lon)),
                URLQueryItem(name: "current", value: "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation"),
                URLQueryItem(name: "daily", value: "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"),
                URLQueryItem(name: "forecast_days", value: "2"),
                URLQueryItem(name: "temperature_unit", value: "fahrenheit"),
                URLQueryItem(name: "wind_speed_unit", value: "mph"),
                URLQueryItem(name: "precipitation_unit", value: "inch"),
                URLQueryItem(name: "timezone", value: "auto")
            ]
            guard let wxURL = wxParts.url else { throw URLError(.badURL) }
            let wx = try await Net.json(from: wxURL) as? [String: Any]
            guard let current = wx?["current"] as? [String: Any],
                  current["temperature_2m"] is NSNumber, current["weather_code"] is NSNumber,
                  current["apparent_temperature"] is NSNumber, current["wind_speed_10m"] is NSNumber else { throw URLError(.cannotParseResponse) }
            let daily = wx?["daily"] as? [String: Any] ?? [:]
            let temp = number(current["temperature_2m"])
            let feel = number(current["apparent_temperature"])
            let wind = number(current["wind_speed_10m"])
            let code = Int(number(current["weather_code"]))
            let condition = conditionName(code)
            let maxArr = daily["temperature_2m_max"] as? [Double] ?? []
            let minArr = daily["temperature_2m_min"] as? [Double] ?? []
            let high = maxArr.first ?? temp
            let low = minArr.first ?? temp
            let payload = String(format: "%.0f|%d|%.0f", temp, code, high)
            if store.lastQuote("weather:\(place)") == payload {
                return DeskResult(desk: .weather, articles: [], skippedOld: 0, skippedDup: 1, sourceErrors: [], sourceChecks: [SourceCheck(source: "Open-Meteo")])
            }

            let title = "\(place): \(Int(temp))°F \(condition)"
            let summary = "Feels like \(Int(feel))° · Wind \(Int(wind)) mph · Today \(Int(high))/\(Int(low))°F"
            let url = "https://open-meteo.com/en/forecast?latitude=\(lat)&longitude=\(lon)"
            let id = FreshnessStore.articleID(url: url + payload, fallback: "wx-\(place)-\(payload)")
            let tags = Hashtagger.tags(desk: .weather, title: title, extra: ["#Forecast"])
            let (tweet, chars) = Hashtagger.tweet(title: title, url: url, tags: tags)
            let article = Article(
                id: id,
                desk: .weather,
                title: title,
                url: url,
                source: "Open-Meteo",
                published: Date(),
                summary: summary,
                tweet: tweet,
                tweetChars: chars,
                hashtags: tags,
                extra: ["city": place]
            )
            return DeskResult(desk: .weather, articles: [article], skippedOld: 0, skippedDup: 0, sourceErrors: [], quoteUpdates: [("weather:\(place)", payload)], sourceChecks: [SourceCheck(source: "Open-Meteo")])
        } catch {
            errors.append("Weather: Open-Meteo unreachable")
            return DeskResult(desk: .weather, articles: [], skippedOld: 0, skippedDup: 0, sourceErrors: errors)
        }
    }

    private static func number(_ value: Any?) -> Double {
        if let n = value as? NSNumber { return n.doubleValue }
        if let s = value as? String { return Double(s) ?? 0 }
        return 0
    }

    private static func conditionName(_ code: Int) -> String {
        switch code {
        case 0: return "Clear"
        case 1, 2: return "Mostly clear"
        case 3: return "Overcast"
        case 45, 48: return "Fog"
        case 51, 53, 55: return "Drizzle"
        case 61, 63, 65: return "Rain"
        case 71, 73, 75, 77: return "Snow"
        case 80, 81, 82: return "Showers"
        case 95, 96, 99: return "Thunderstorm"
        default: return "Mixed"
        }
    }
}
