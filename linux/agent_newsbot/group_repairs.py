"""Audited, idempotent manual grouping. Historical events and aliases never change."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from .library import LibraryConflict, decorate
from .store import canonical_url
from .hashtags import tweet

REPAIR_SCHEMA = "CREATE TABLE IF NOT EXISTS group_repairs(id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL)"


def validate_request(change):
    common = {"operation_id", "action", "source_id", "expected_revision", "expected_editorial_version", "reason"}
    action = change.get("action")
    required = common | ({"source_urls"} if action == "split" else {"target_id", "target_revision", "target_editorial_version"})
    if action not in ("split", "merge") or set(change) != required:
        raise ValueError("invalid repair fields")
    for key in ("operation_id", "source_id") + (("target_id",) if action == "merge" else ()):
        if not isinstance(change[key], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", change[key]):
            raise ValueError("invalid repair identity")
    if not isinstance(change["reason"], str) or not change["reason"].strip() or len(change["reason"]) > 1000:
        raise ValueError("repair reason is required (1-1000 characters)")
    for key in required & {"expected_revision", "expected_editorial_version", "target_revision", "target_editorial_version"}:
        if type(change[key]) is not int or not 0 <= change[key] <= 2**53 - 1: raise ValueError("invalid repair version")
    if action == "split":
        urls = change["source_urls"]
        if not isinstance(urls, list) or not 1 <= len(urls) <= 100 or any(not isinstance(u, str) or len(u) > 8192 for u in urls):
            raise ValueError("select 1-100 source URLs")
        if len(set(urls)) != len(urls): raise ValueError("duplicate source URL")


class GroupRepairMixin:
    def repair_group(self, change):
        from .stories import encoded, reports_disagree
        validate_request(change)
        request = encoded(change)
        if len(request.encode()) > 65536: raise ValueError("repair request too large")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT request,result FROM group_repairs WHERE id=?", (change["operation_id"],)).fetchone()
            if prior:
                if prior[0] != request: raise LibraryConflict("operation ID already used for a different repair")
                return json.loads(prior[1])

            def load(sid, revision, version):
                row = db.execute("SELECT payload FROM stories WHERE id=?", (sid,)).fetchone()
                if not row: raise LookupError("story not found; use its canonical story ID")
                story = json.loads(row[0])
                raw = db.execute("SELECT payload FROM editorial WHERE story_id=?", (sid,)).fetchone()
                state = decorate(story, json.loads(raw[0]) if raw else {})["editorial"]
                if story.get("merged_into"): raise LibraryConflict("story was merged; reload its destination")
                if story.get("revision") != revision or state["version"] != version:
                    raise LibraryConflict("story or editorial state changed; reload before repairing")
                sources = story.get("sources", [])
                urls = [s["url"] for s in sources]
                if not urls or len(urls) != len(set(urls)): raise LibraryConflict("invalid existing source membership")
                for url in urls:
                    owner = db.execute("SELECT story_id FROM members WHERE url=?", (canonical_url(url),)).fetchone()
                    if not owner or owner[0] != sid: raise LibraryConflict("source membership changed; repair aborted")
                return story, state

            source, source_state = load(change["source_id"], change["expected_revision"], change["expected_editorial_version"])
            before = [dict(story=copy.deepcopy(source), editorial=copy.deepcopy(source_state))]
            now = datetime.now(timezone.utc); stamp = now.isoformat()
            op = change["operation_id"]
            if change["action"] == "split":
                chosen = set(change["source_urls"]); all_urls = {s["url"] for s in source["sources"]}
                if not chosen < all_urls: raise ValueError("split must select a proper subset of the current source URLs")
                child_id = hashlib.sha256(("group-repair:" + op).encode()).hexdigest()[:24]
                if db.execute("SELECT 1 FROM stories WHERE id=? UNION ALL SELECT 1 FROM identity_aliases WHERE alias=?", (child_id, child_id)).fetchone():
                    raise LibraryConflict("generated story identity already exists")
                child = copy.deepcopy(source)
                child.update(id=child_id, story_id=child_id, split_from=source["id"], revision=0, split_children=[])
                child.pop("event_seq", None)
                child["sources"] = [s for s in source["sources"] if s["url"] in chosen]
                source["sources"] = [s for s in source["sources"] if s["url"] not in chosen]
                source["split_children"] = sorted(set(source.get("split_children", [])) | {child_id})
                for url, in db.execute("SELECT url FROM members WHERE story_id=?", (source["id"],)).fetchall():
                    if canonical_url(url) in chosen: db.execute("UPDATE members SET story_id=? WHERE url=?", (child_id, url))
                # A new group has no reviewed draft. Original drafts remain with their original ID.
                child["hashtags"] = []
                after = [(source, source_state), (child, decorate(child, {})["editorial"])]
            else:
                if source["id"] == change["target_id"]: raise ValueError("cannot merge a story into itself")
                target, target_state = load(change["target_id"], change["target_revision"], change["target_editorial_version"])
                before.append(dict(story=copy.deepcopy(target), editorial=copy.deepcopy(target_state)))
                if {s["url"] for s in source["sources"]} & {s["url"] for s in target["sources"]}:
                    raise LibraryConflict("overlapping memberships require investigation")
                target["sources"] += copy.deepcopy(source["sources"])
                target["desks"] = sorted(set(source.get("desks", [source["desk"]])) | set(target.get("desks", [target["desk"]])))
                target["observation_version"] = max(source.get("observation_version", 0), target.get("observation_version", 0))
                source["merged_into"] = target["id"]
                db.execute("UPDATE members SET story_id=? WHERE story_id=?", (target["id"], source["id"]))
                after = [(source, source_state), (target, target_state)]

            receipt = dict(operation_id=op, action=change["action"], reason=change["reason"], created_at=stamp, before=before, after=[])
            for story, state in after:
                story["sources"].sort(key=lambda s: s["url"])
                primary = next((s for s in story["sources"] if s["url"] == story["url"]), story["sources"][0])
                if primary["url"] != story["url"]: story["hashtags"] = []
                for key in ("url", "title", "summary", "source", "published"): story[key] = primary.get(key, story.get(key, ""))
                story["tweet"], story["tweet_chars"] = tweet(story["title"], story["url"], story.get("hashtags", []))
                story.update(grouping_locked=True, grouping_policy="manual-v1", last_repair_id=op,
                             revision=story.get("revision", 0)+1, change="group_repaired", updated_at=stamp,
                             changed_fields=["sources", "grouping_policy", "editorial_review"],
                             disagreement=reports_disagree(story["sources"]), review_note="Manual grouping repair; review sources and saved drafts before use.")
                story.pop("display_desks", None)
                state.update(status="needs_review", approved_revision=None, version=state["version"]+1, updated_at=stamp)
                state["platforms"] = {name: {"status": "needs_review", "approved_revision": None} for name in ("youtube", "x", "tiktok", "narration")}
                # Clear the old event pointer before publishing the immutable new event.
                story.pop("event_seq", None)
                event = db.execute("INSERT INTO events(story_id,payload) VALUES (?,?)", (story["id"], encoded(story)))
                story["event_seq"] = event.lastrowid
                db.execute("INSERT OR REPLACE INTO stories VALUES (?,?,?)", (story["id"], encoded(story), now.timestamp()))
                db.execute("INSERT OR REPLACE INTO editorial VALUES (?,?)", (story["id"], encoded(state)))
                receipt["after"].append(dict(id=story["id"], revision=story["revision"], editorial_version=state["version"], event_seq=event.lastrowid))
            result = encoded(receipt)
            if len(result.encode()) > 4 * 1024 * 1024: raise ValueError("repair evidence exceeds 4 MiB; no changes saved")
            db.execute("INSERT INTO group_repairs VALUES (?,?,?,?)", (op, request, result, now.timestamp()))
            from .alerts import record_alerts
            record_alerts(db, [row["id"] for row in receipt["after"]])
            return receipt

    def repair_receipt(self, operation_id):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT result FROM group_repairs WHERE id=?", (operation_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def reconcile_groups(self, stories):
        """Hydrate current group identities for a snapshot, including split children.

        Callers still apply expiry/desk caps. Does not rewrite persisted historical snapshots.
        """
        with self.lock, self.connect() as db:
            db.execute("BEGIN")
            visited, result = set(), []
            queue = [dict(s) for s in stories]
            while queue:
                fallback = queue.pop(0); sid = fallback["id"]
                if sid in visited: continue
                visited.add(sid)
                if len(visited) > 1000: raise ValueError("group reconciliation exceeds 1000 identities")
                row = db.execute("SELECT payload FROM stories WHERE id=?", (sid,)).fetchone()
                story = json.loads(row[0]) if row else fallback
                for related in story.get("split_children", []): queue.append({"id": related})
                if story.get("merged_into"):
                    queue.append({"id": story["merged_into"]}); continue
                if "url" not in story: raise LibraryConflict("repair references a missing story")
                story.pop("display_desks", None)
                result.append(story)
            return result
