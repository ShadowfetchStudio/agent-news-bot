import Foundation
import CoreFoundation
import CryptoKit

/// Append-only repair receipts; old story IDs, revisions, aliases and consumer positions survive.
extension StoryStore {
    func repairGroup(_ change: [String: Any]) throws -> [String: Any] {
        let action = change["action"] as? String ?? ""
        let common: Set<String> = ["operation_id", "action", "source_id", "expected_revision", "expected_editorial_version", "reason"]
        let required = common.union(action == "split" ? ["source_urls"] : ["target_id", "target_revision", "target_editorial_version"])
        guard ["split", "merge"].contains(action), Set(change.keys) == required else { throw LibraryFailure.invalid("invalid repair fields") }
        for key in ["operation_id", "source_id"] + (action == "merge" ? ["target_id"] : []) {
            guard let value = change[key] as? String, value.range(of: "^[A-Za-z0-9_.-]{1,64}$", options: .regularExpression) != nil else { throw LibraryFailure.invalid("invalid repair identity") }
        }
        guard let reason = change["reason"] as? String, !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, reason.unicodeScalars.count <= 1000 else { throw LibraryFailure.invalid("repair reason is required (1-1000 characters)") }
        for key in required.intersection(["expected_revision", "expected_editorial_version", "target_revision", "target_editorial_version"]) {
            guard let value = change[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), !["d", "f"].contains(String(cString: value.objCType)), value.doubleValue >= 0, value.doubleValue <= 9007199254740991 else { throw LibraryFailure.invalid("invalid repair version") }
        }
        if action == "split" {
            guard let urls = change["source_urls"] as? [String], (1...100).contains(urls.count), Set(urls).count == urls.count, urls.allSatisfy({ $0.unicodeScalars.count <= 8192 }) else { throw LibraryFailure.invalid("select 1-100 distinct source URLs") }
        }
        let request = try encode(change), op = change["operation_id"] as! String
        guard request.utf8.count <= 65536 else { throw LibraryFailure.invalid("repair request too large") }
        return try withDB { db in
            if let prior = try run(db, "SELECT request,result FROM group_repairs WHERE id=?", [op]).first {
                guard prior[0] == request else { throw LibraryFailure.conflict("operation ID already used for a different repair") }
                return try decode(prior[1])
            }
            func load(_ id: String, _ revision: Int, _ version: Int) throws -> (story: [String: Any], state: [String: Any]) {
                guard let row = try run(db, "SELECT payload FROM stories WHERE id=?", [id]).first else { throw LibraryFailure.missing }
                let story = try decode(row[0])
                let raw = try run(db, "SELECT payload FROM editorial WHERE story_id=?", [id]).first.map { try decode($0[0]) } ?? [:]
                let state = LibraryModel.decorate(story, raw)["editorial"] as! [String: Any]
                guard story["merged_into"] == nil else { throw LibraryFailure.conflict("story was merged; reload its destination") }
                guard story["revision"] as? Int == revision, state["version"] as? Int == version else { throw LibraryFailure.conflict("story or editorial state changed; reload before repairing") }
                let sources = story["sources"] as? [[String: Any]] ?? [], urls = sources.compactMap { $0["url"] as? String }
                guard !urls.isEmpty, urls.count == sources.count, Set(urls).count == urls.count else { throw LibraryFailure.conflict("invalid existing source membership") }
                for url in urls {
                    guard try run(db, "SELECT story_id FROM members WHERE url=?", [CanonicalURL.fingerprint(url)]).first?.first == id else { throw LibraryFailure.conflict("source membership changed; repair aborted") }
                }
                return (story, state)
            }
            var source = try load(change["source_id"] as! String, change["expected_revision"] as! Int, change["expected_editorial_version"] as! Int)
            let sid = source.story["id"] as! String
            var before: [[String: Any]] = [["story": source.story, "editorial": source.state]]
            let now = Date(), stamp = ISO8601DateFormatter().string(from: Date())
            var after: [(story: [String: Any], state: [String: Any])]
            if action == "split" {
                let chosen = Set(change["source_urls"] as! [String]), sources = source.story["sources"] as! [[String: Any]]
                let urls = Set(sources.compactMap { $0["url"] as? String })
                guard chosen.isStrictSubset(of: urls) else { throw LibraryFailure.invalid("split must select a proper subset of the current source URLs") }
                let childID = String(SHA256.hash(data: Data(("group-repair:" + op).utf8)).map { String(format: "%02x", $0) }.joined().prefix(24))
                guard try run(db, "SELECT 1 FROM stories WHERE id=? UNION ALL SELECT 1 FROM identity_aliases WHERE alias=?", [childID, childID]).isEmpty else { throw LibraryFailure.conflict("generated story identity already exists") }
                var child = source.story
                child["id"] = childID; child["story_id"] = childID; child["split_from"] = sid; child["revision"] = 0
                child["split_children"] = [String](); child.removeValue(forKey: "event_seq")
                child["sources"] = sources.filter { chosen.contains($0["url"] as! String) }
                child["hashtags"] = [String]()
                source.story["sources"] = sources.filter { !chosen.contains($0["url"] as! String) }
                source.story["split_children"] = Set(source.story["split_children"] as? [String] ?? []).union([childID]).sorted()
                for row in try run(db, "SELECT url FROM members WHERE story_id=?", [sid]) where chosen.contains(CanonicalURL.fingerprint(row[0])) {
                    try run(db, "UPDATE members SET story_id=? WHERE url=?", [childID, row[0]])
                }
                after = [source, (child, LibraryModel.decorate(child, [:])["editorial"] as! [String: Any])]
            } else {
                let targetID = change["target_id"] as! String
                guard targetID != sid else { throw LibraryFailure.invalid("cannot merge a story into itself") }
                var target = try load(targetID, change["target_revision"] as! Int, change["target_editorial_version"] as! Int)
                before.append(["story": target.story, "editorial": target.state])
                let ss = source.story["sources"] as! [[String: Any]], ts = target.story["sources"] as! [[String: Any]]
                guard Set(ss.compactMap { $0["url"] as? String }).isDisjoint(with: ts.compactMap { $0["url"] as? String }) else { throw LibraryFailure.conflict("overlapping memberships require investigation") }
                target.story["sources"] = ts + ss
                target.story["desks"] = Set(source.story["desks"] as? [String] ?? [source.story["desk"] as! String]).union(target.story["desks"] as? [String] ?? [target.story["desk"] as! String]).sorted()
                target.story["observation_version"] = max(source.story["observation_version"] as? Int ?? 0, target.story["observation_version"] as? Int ?? 0)
                source.story["merged_into"] = targetID
                try run(db, "UPDATE members SET story_id=? WHERE story_id=?", [targetID, sid])
                after = [source, target]
            }
            var receipt: [String: Any] = ["operation_id": op, "action": action, "reason": reason, "created_at": stamp, "before": before]
            var refs: [[String: Any]] = []
            for item in after {
                var story = item.story, state = item.state
                let sources = (story["sources"] as! [[String: Any]]).sorted { ($0["url"] as! String) < ($1["url"] as! String) }
                story["sources"] = sources
                let primary = sources.first { $0["url"] as? String == story["url"] as? String } ?? sources[0]
                if primary["url"] as? String != story["url"] as? String { story["hashtags"] = [String]() }
                for key in ["url", "title", "summary", "source", "published"] { story[key] = primary[key] ?? story[key] ?? "" }
                let (post, chars) = Hashtagger.tweet(title: story["title"] as! String, url: story["url"] as! String, tags: story["hashtags"] as? [String] ?? [])
                story["tweet"] = post; story["tweet_chars"] = chars
                story["grouping_locked"] = true; story["grouping_policy"] = "manual-v1"; story["last_repair_id"] = op
                story["revision"] = (story["revision"] as? Int ?? 0) + 1; story["change"] = "group_repaired"; story["updated_at"] = stamp
                story["changed_fields"] = ["sources", "grouping_policy", "editorial_review"]
                story["disagreement"] = disagree(sources); story["review_note"] = "Manual grouping repair; review sources and saved drafts before use."
                story.removeValue(forKey: "display_desks"); story.removeValue(forKey: "event_seq")
                state["status"] = "needs_review"; state["approved_revision"] = NSNull()
                state["platforms"] = Dictionary(uniqueKeysWithValues: LibraryModel.platforms.keys.map { ($0, ["status": "needs_review", "approved_revision": NSNull()] as [String: Any]) })
                state["version"] = (state["version"] as? Int ?? 0) + 1; state["updated_at"] = stamp
                let id = story["id"] as! String
                try run(db, "INSERT INTO events(story_id,payload) VALUES (?,?)", [id, try encode(story)])
                let seq = Int(try run(db, "SELECT last_insert_rowid()")[0][0])!
                story["event_seq"] = seq
                try run(db, "INSERT OR REPLACE INTO stories VALUES (?,?,?)", [id, try encode(story), String(now.timeIntervalSince1970)])
                try run(db, "INSERT OR REPLACE INTO editorial VALUES (?,?)", [id, try encode(state)])
                refs.append(["id": id, "revision": story["revision"]!, "editorial_version": state["version"]!, "event_seq": seq])
            }
            receipt["after"] = refs
            let result = try encode(receipt)
            guard result.utf8.count <= 4 * 1024 * 1024 else { throw LibraryFailure.invalid("repair evidence exceeds 4 MiB; no changes saved") }
            try run(db, "INSERT INTO group_repairs VALUES (?,?,?,?)", [op, request, result, String(now.timeIntervalSince1970)])
            return receipt
        }
    }

    func repairReceipt(_ operationID: String) throws -> [String: Any]? {
        try withDB { db in try run(db, "SELECT result FROM group_repairs WHERE id=?", [operationID]).first.map { try decode($0[0]) } }
    }

    /// Callers apply expiry and desk caps after hydrating live identities. Historical snapshots stay intact.
    func reconcileGroups(_ stories: [[String: Any]]) throws -> [[String: Any]] {
        try withDB { db in
            var queue = stories, visited = Set<String>(), result: [[String: Any]] = [], position = 0
            while position < queue.count {
                let fallback = queue[position]; position += 1
                guard let id = fallback["id"] as? String else { throw LibraryFailure.invalid("missing snapshot story identity") }
                if !visited.insert(id).inserted { continue }
                guard visited.count <= 1000 else { throw LibraryFailure.invalid("group reconciliation exceeds 1000 identities") }
                var story = try run(db, "SELECT payload FROM stories WHERE id=?", [id]).first.map { try decode($0[0]) } ?? fallback
                for related in story["split_children"] as? [String] ?? [] { queue.append(["id": related]) }
                if let destination = story["merged_into"] as? String { queue.append(["id": destination]); continue }
                guard story["url"] is String else { throw LibraryFailure.conflict("repair references a missing story") }
                story.removeValue(forKey: "display_desks")
                result.append(story)
            }
            return result
        }
    }
}
