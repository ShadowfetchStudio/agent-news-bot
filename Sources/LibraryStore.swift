import Foundation
import CoreFoundation

enum LibraryFailure: Error { case invalid(String), conflict(String), missing }
enum LibraryModel {
    static let states: Set<String> = ["unreviewed", "draft", "needs_review", "approved", "rejected"]
    static let fields = ["youtube_title": 100, "youtube_description": 5000, "x_post": 12000, "tiktok_caption": 2200, "narration": 12000]
    static let platforms = ["youtube": ["youtube_title", "youtube_description"], "x": ["x_post"], "tiktok": ["tiktok_caption"], "narration": ["narration"]]
    static func platformStates(_ story: [String: Any], _ state: [String: Any]) -> [String: [String: Any]] {
        let saved = state["platforms"] as? [String: [String: Any]] ?? [:]
        return Dictionary(uniqueKeysWithValues: platforms.keys.map { name in
            var row = saved[name] ?? ["status": state["status"] ?? "unreviewed", "approved_revision": state["approved_revision"] ?? NSNull()]
            if row["status"] as? String == "approved", row["approved_revision"] as? Int != story["revision"] as? Int || story["merged_into"] != nil { row["status"] = "needs_review" }
            return (name, row)
        })
    }
    static func aggregate(_ platforms: [String: [String: Any]]) -> String {
        let states = Set(platforms.values.compactMap { $0["status"] as? String })
        if states.contains("needs_review") { return "needs_review" }
        return states.count == 1 ? states.first! : "draft"
    }
    static func clipped(_ text: String, _ count: Int) -> String { String(String.UnicodeScalarView(text.unicodeScalars.prefix(count))) }
    static func templates(_ story: [String: Any]) -> [String: String] {
        let title = story["title"] as? String ?? "", summary = story["summary"] as? String ?? "", url = story["url"] as? String ?? ""
        let urls = (story["sources"] as? [[String: Any]] ?? []).compactMap { $0["url"] as? String }
        let tags = story["hashtags"] as? [String] ?? []
        let (post, _) = Hashtagger.tweet(title: title, url: url, tags: tags)
        return ["youtube_title": clipped(title, 100), "youtube_description": clipped("\(title)\n\n\(summary)\n\nSources:\n" + (urls.isEmpty ? url : urls.joined(separator: "\n")), 5000),
                "x_post": post, "tiktok_caption": clipped(title + "\n\n" + tags.prefix(3).joined(separator: " "), 2200), "narration": clipped(title + "\n\n" + summary, 12000)]
    }
    static func decorate(_ story: [String: Any], _ editorial: [String: Any]) -> [String: Any] {
        var state = editorial, result = story
        state["version"] = state["version"] ?? 0
        state["status"] = state["status"] ?? "unreviewed"; state["bookmarked"] = state["bookmarked"] ?? false
        state["drafts"] = state["drafts"] ?? [String: String](); state["notes"] = state["notes"] ?? ""
        if state["status"] as? String == "approved", state["approved_revision"] as? Int != story["revision"] as? Int { state["status"] = "needs_review" }
        let platforms = platformStates(story, state)
        state["platforms"] = platforms; state["status"] = aggregate(platforms)
        let count = (story["sources"] as? [Any] ?? []).count, disagreement = story["disagreement"] as? Bool == true
        result["editorial"] = state
        result["review_priority"] = min(count, 5) * 5 + (disagreement ? 15 : 0) + (story["change"] as? String == "updated" ? 10 : 0)
        result["quality_notes"] = ["\(count) source links; independence has not been verified.", "Provider credibility/importance scores are assessments, not fact checks.", "Media reuse rights are unknown until reviewed."] + (disagreement ? ["Conflicting or changed headlines need review."] : [])
        do { try ArticleURL.requireSafeStory(story); result["actionable_links_safe"] = true }
        catch {
            result["actionable_links_safe"] = false
            result["quality_notes"] = (result["quality_notes"] as? [String] ?? []) + ["Invalid historical source link retained for evidence. Opening, drafting, approval and export of unsafe links are blocked."]
        }
        return result
    }
    static func query(_ query: [String: String]) throws -> (sql: String, values: [String], limit: Int, offset: Int) {
        let allowed: Set<String> = ["q", "entity", "desk", "source", "provider", "status", "bookmarked", "include_muted", "include_merged", "sort", "limit", "offset"]
        guard Set(query.keys).isSubset(of: allowed), query.values.allSatisfy({ $0.unicodeScalars.count <= 300 }),
              let limit = Int(query["limit"] ?? "50"), (1...100).contains(limit), let offset = Int(query["offset"] ?? "0"), (0...100000).contains(offset),
              ["recent", "priority"].contains(query["sort"] ?? "recent") else { throw LibraryFailure.invalid("invalid library filter or pagination") }
        guard ["bookmarked", "include_muted", "include_merged"].allSatisfy({ ["true", "false"].contains(query[$0] ?? "false") }) else {
            throw LibraryFailure.invalid("boolean filters must be true or false")
        }
        var clauses = ["1=1"], values: [String] = []
        if query["include_merged"] != "true" { clauses.append("json_extract(s.payload,'$.merged_into') IS NULL") }
        for key in ["q", "entity"] { if let value = query[key], !value.isEmpty {
            clauses.append("instr(lower(coalesce(json_extract(s.payload,'$.title'),'') || ' ' || coalesce(json_extract(s.payload,'$.summary'),'') || ' ' || coalesce(json_extract(s.payload,'$.sources'),'')),lower(?))>0"); values.append(value)
        } }
        if let value = query["desk"], !value.isEmpty {
            clauses.append("EXISTS(SELECT 1 FROM json_each(coalesce(json_extract(s.payload,'$.desks'),json_array(json_extract(s.payload,'$.desk')))) WHERE value=?)"); values.append(value)
        }
        if let value = query["source"], !value.isEmpty {
            clauses.append("EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src WHERE instr(lower(json_extract(src.value,'$.source')),lower(?))>0 OR instr(lower(json_extract(src.value,'$.url')),lower(?))>0)"); values += [value, value]
        }
        if let value = query["provider"], !value.isEmpty {
            clauses.append("EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src, json_each(src.value,'$.providers') p WHERE p.value=?)"); values.append(value)
        }
        if query["bookmarked"] == "true" { clauses.append("coalesce(json_extract(e.payload,'$.bookmarked'),0)=1") }
        if let value = query["status"], !value.isEmpty {
            guard states.contains(value) else { throw LibraryFailure.invalid("invalid editorial status") }
            clauses.append("CASE WHEN (json_extract(e.payload,'$.status')='approved' AND coalesce(json_extract(e.payload,'$.approved_revision'),0)!=json_extract(s.payload,'$.revision')) OR EXISTS(SELECT 1 FROM json_each(e.payload,'$.platforms') p WHERE json_extract(p.value,'$.status')='approved' AND coalesce(json_extract(p.value,'$.approved_revision'),0)!=json_extract(s.payload,'$.revision')) THEN 'needs_review' ELSE coalesce(json_extract(e.payload,'$.status'),'unreviewed') END=?"); values.append(value)
        }
        if query["include_muted"] != "true" {
            clauses.append("(NOT EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources')) OR EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src WHERE NOT EXISTS(SELECT 1 FROM library_preferences p WHERE p.kind='mute' AND lower(p.name)=lower(json_extract(src.value,'$.source')))))")
        }
        let priority = "min(coalesce(json_array_length(json_extract(s.payload,'$.sources')),0),5)*5 + CASE WHEN json_extract(s.payload,'$.disagreement')=1 THEN 15 ELSE 0 END + CASE WHEN json_extract(s.payload,'$.change')='updated' THEN 10 ELSE 0 END"
        let order = (query["sort"] == "priority" ? priority + " DESC," : "") + "coalesce(json_extract(s.payload,'$.event_seq'),0) DESC,s.id"
        return ("SELECT s.payload,coalesce(e.payload,'{}') FROM stories s LEFT JOIN editorial e ON e.story_id=s.id WHERE " + clauses.joined(separator: " AND ") + " ORDER BY " + order + " LIMIT ? OFFSET ?", values + [String(limit + 1), String(offset)], limit, offset)
    }
}

extension StoryStore {
    func library(_ query: [String: String] = [:]) throws -> [String: Any] {
        let request = try LibraryModel.query(query)
        return try withDB { db in
            let rows = try run(db, request.sql, request.values)
            return ["stories": try rows.prefix(request.limit).map { LibraryModel.decorate(try decode($0[0]), try decode($0[1])) },
                    "limit": request.limit, "offset": request.offset, "next_offset": rows.count > request.limit ? request.offset + request.limit : NSNull()]
        }
    }
    func editorial(_ id: String) throws -> [String: Any]? {
        try withDB { db in
            guard let row = try run(db, "SELECT payload,id FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", [id,id]).first else { return nil }
            let state = try run(db, "SELECT payload FROM editorial WHERE story_id=?", [row[1]]).first.map { try decode($0[0]) } ?? [:]
            return LibraryModel.decorate(try decode(row[0]), state)
        }
    }
    func saveEditorial(_ id: String, change: [String: Any]) throws -> [String: Any] {
        guard Set(change.keys).isSubset(of: ["expected_revision", "expected_editorial_version", "bookmarked", "status", "notes", "drafts", "draft_patch", "platform_status", "generate"]) else { throw LibraryFailure.invalid("unknown editorial field") }
        guard change["platform_status"] == nil || change["status"] == nil else { throw LibraryFailure.invalid("choose platform_status or whole-package status") }
        guard [change["drafts"] != nil, change["draft_patch"] != nil, change["generate"] as? Bool == true].filter({ $0 }).count <= 1 else { throw LibraryFailure.invalid("choose one draft operation") }
        return try withDB { db in
            guard let row = try run(db, "SELECT payload,id FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", [id,id]).first else { throw LibraryFailure.missing }
            let story = try decode(row[0]), sid = row[1], revision = story["revision"] as? Int ?? 0
            guard story["merged_into"] == nil else { throw LibraryFailure.conflict("story was merged; edit its destination instead") }
            guard let expected = change["expected_revision"] as? NSNumber, CFGetTypeID(expected) != CFBooleanGetTypeID(), expected.intValue == revision,
                  !["d", "f"].contains(String(cString: expected.objCType)), expected.doubleValue == Double(revision) else { throw LibraryFailure.conflict("story changed; reload before saving") }
            let raw = try run(db, "SELECT payload FROM editorial WHERE story_id=?", [sid]).first.map { try decode($0[0]) } ?? [:]
            var state = LibraryModel.decorate(story, raw)["editorial"] as! [String: Any]
            guard let version = change["expected_editorial_version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
                  !["d", "f"].contains(String(cString: version.objCType)), version.intValue == state["version"] as? Int else { throw LibraryFailure.conflict("editorial changes exist; reload before saving") }
            if let generate = change["generate"] {
                guard let number = generate as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { throw LibraryFailure.invalid("generate must be boolean") }
            }
            if let value = change["bookmarked"] {
                guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { throw LibraryFailure.invalid("bookmarked must be boolean") }
                state["bookmarked"] = number.boolValue
            }
            if let value = change["notes"] {
                guard let notes = value as? String, notes.unicodeScalars.count <= 12000 else { throw LibraryFailure.invalid("notes too long") }
                state["notes"] = notes
            }
            var platforms = state["platforms"] as! [String: [String: Any]]
            if change["generate"] as? Bool == true || change["drafts"] != nil || change["draft_patch"] != nil {
                do { try ArticleURL.requireSafeStory(story) } catch { throw LibraryFailure.invalid("Saved story contains an invalid source link; drafting is disabled.") }
                let previousDrafts = state["drafts"] as? [String: String] ?? [:]
                var value: Any? = change["generate"] as? Bool == true ? LibraryModel.templates(story) : change["drafts"]
                if let patchValue = change["draft_patch"] {
                    guard let patch = patchValue as? [String: String], !patch.isEmpty, Set(patch.keys).isSubset(of: Set(LibraryModel.fields.keys)) else { throw LibraryFailure.invalid("invalid draft patch") }
                    var updated = Dictionary(uniqueKeysWithValues: LibraryModel.fields.keys.map { ($0, previousDrafts[$0] ?? "") })
                    updated.merge(patch) { _, new in new }; value = updated
                }
                guard let drafts = value as? [String: String], Set(drafts.keys) == Set(LibraryModel.fields.keys),
                      drafts.allSatisfy({ $0.value.unicodeScalars.count <= LibraryModel.fields[$0.key]! }) else { throw LibraryFailure.invalid("all five bounded draft fields are required") }
                if let post = drafts["x_post"], !post.isEmpty {
                    let measured: [String: Any]
                    do { measured = try XText.request(["op": "measure", "text": post]) }
                    catch { throw LibraryFailure.invalid("X draft validation unavailable or over 280 weighted characters") }
                    if measured["valid"] as? Bool != true { throw LibraryFailure.invalid("X draft is invalid or over 280 weighted characters") }
                }
                for (name, fields) in LibraryModel.platforms {
                    if change["generate"] as? Bool == true || fields.contains(where: { drafts[$0] != (previousDrafts[$0] ?? "") }) {
                        platforms[name] = ["status": "draft", "approved_revision": NSNull()]
                    }
                }
                state["drafts"] = drafts; state["source_revision"] = revision; state["status"] = LibraryModel.aggregate(platforms)
                if state["status"] as? String != "approved" { state["approved_revision"] = NSNull() }
            }
            if let value = change["status"] {
                guard let status = value as? String, LibraryModel.states.contains(status) else { throw LibraryFailure.invalid("invalid editorial status") }
                if status == "approved" {
                    do { try ArticleURL.requireSafeStory(story) } catch { throw LibraryFailure.invalid("Saved story contains an invalid source link; approval is disabled.") }
                    guard state["source_revision"] as? Int == revision else { throw LibraryFailure.conflict("drafts need review against the current revision") }
                    let drafts = state["drafts"] as? [String: String] ?? [:]
                    guard LibraryModel.fields.keys.allSatisfy({ !(drafts[$0] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else { throw LibraryFailure.invalid("complete the platform drafts before approval") }
                    state["approved_revision"] = revision
                } else { state["approved_revision"] = NSNull() }
                state["status"] = status
                platforms = Dictionary(uniqueKeysWithValues: LibraryModel.platforms.keys.map { ($0, ["status": status, "approved_revision": state["approved_revision"] ?? NSNull()]) })
            }
            if let value = change["platform_status"] {
                guard let decisions = value as? [String: String], !decisions.isEmpty, Set(decisions.keys).isSubset(of: Set(LibraryModel.platforms.keys)) else { throw LibraryFailure.invalid("invalid platform decision") }
                for (name, status) in decisions {
                    guard LibraryModel.states.contains(status) else { throw LibraryFailure.invalid("invalid platform status") }
                    if status == "approved" {
                        do { try ArticleURL.requireSafeStory(story) } catch { throw LibraryFailure.invalid("Saved story contains an invalid source link; approval is disabled.") }
                        let drafts = state["drafts"] as? [String: String] ?? [:]
                        guard LibraryModel.platforms[name]!.allSatisfy({ !(drafts[$0] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else { throw LibraryFailure.invalid("complete this platform's draft before approval") }
                        if name == "x" {
                            let measured: [String: Any]
                            do { measured = try XText.request(["op": "measure", "text": drafts["x_post"] ?? ""]) }
                            catch { throw LibraryFailure.invalid("X draft validation unavailable or over 280 weighted characters") }
                            if measured["valid"] as? Bool != true { throw LibraryFailure.invalid("X draft is invalid or over 280 weighted characters") }
                        }
                    }
                    platforms[name] = ["status": status, "approved_revision": status == "approved" ? revision : NSNull()]
                }
                state["status"] = LibraryModel.aggregate(platforms)
                state["approved_revision"] = state["status"] as? String == "approved" ? revision : NSNull()
                if state["status"] as? String == "approved" { state["source_revision"] = revision }
            }
            state["platforms"] = platforms
            state["version"] = (state["version"] as? Int ?? 0) + 1
            state["updated_at"] = ISO8601DateFormatter().string(from: Date())
            try run(db, "INSERT OR REPLACE INTO editorial VALUES (?,?)", [sid, try encode(state)])
            return LibraryModel.decorate(story, state)
        }
    }
    func librarySettings(kind: String? = nil, name: String? = nil, value: Any? = nil, write: Bool = false) throws -> [String: Any] {
        try withDB(readOnly: !write) { db in
            if write {
                guard let kind, ["search", "mute"].contains(kind), let name = name?.trimmingCharacters(in: .whitespacesAndNewlines),
                      !name.isEmpty, name.unicodeScalars.count <= 200 else { throw LibraryFailure.invalid("invalid preference") }
                if value == nil || value is NSNull { try run(db, "DELETE FROM library_preferences WHERE kind=? AND name=?", [kind, name]) }
                else {
                    if kind == "search" {
                        guard let query = value as? [String: String] else { throw LibraryFailure.invalid("search must be filters") }
                        _ = try LibraryModel.query(query)
                    } else {
                        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID(), number.boolValue else { throw LibraryFailure.invalid("mute must be true or null") }
                    }
                    // Preferences can be a JSON boolean; encode through a wrapper to allow fragments.
                    let encoded = String(decoding: try JSONSerialization.data(withJSONObject: value!, options: [.fragmentsAllowed, .sortedKeys]), as: UTF8.self)
                    try run(db, "INSERT OR REPLACE INTO library_preferences VALUES (?,?,?)", [kind, name, encoded])
                }
            }
            return ["preferences": try run(db, "SELECT kind,name,payload FROM library_preferences ORDER BY kind,name").map { row in
                ["kind": row[0], "name": row[1], "value": try JSONSerialization.jsonObject(with: Data(row[2].utf8), options: [.fragmentsAllowed])] as [String: Any]
            }]
        }
    }
    func researchExport(_ id: String) throws -> [String: Any] {
        let context = try researchContext(id), story = context.story
        guard story["merged_into"] == nil else { throw LibraryFailure.conflict("story was merged; export its destination instead") }
        do { try ArticleURL.requireSafeStory(story) } catch { throw LibraryFailure.invalid("Saved story contains an invalid source link; export is disabled.") }
        var seen = Set<String>()
        var urls = (story["sources"] as? [[String: Any]] ?? []).compactMap { $0["url"] as? String }.filter { seen.insert($0).inserted }
        if urls.isEmpty { urls = [story["url"] as? String ?? ""] }
        return ["schema_version": 1, "kind": "agent-news-bot-research", "story": story,
                "approved": (story["editorial"] as? [String: Any])?["status"] as? String == "approved", "source_urls": urls,
                "platform_approvals": (story["editorial"] as? [String: Any])?["platforms"] ?? [:],
                "brief": EditorialBrief.build(story, previous: context.previous),
                "videobot": ["action": "inspect", "urls": urls, "instructions": "Paste these source URLs into VideoBot Inspect URLs, review discovered media, then choose what to queue. No media availability or reuse permission is implied."],
                "rights": "unknown; review each source before reuse", "publishing": "manual only"]
    }
}
