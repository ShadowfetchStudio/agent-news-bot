"""Persistent research library and revision-bound editorial decisions. Never publishes."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from .hashtags import tweet
from .xtext import measure
from .url_safety import require_safe_story, safe_article_url

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS editorial(story_id TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS library_preferences(kind TEXT NOT NULL,name TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,name))",
]
STATES = {"unreviewed", "draft", "needs_review", "approved", "rejected"}
FIELDS = {"youtube_title": 100, "youtube_description": 5000, "x_post": 12000, "tiktok_caption": 2200, "narration": 12000}
PLATFORMS = {"youtube": ("youtube_title", "youtube_description"), "x": ("x_post",),
             "tiktok": ("tiktok_caption",), "narration": ("narration",)}
class LibraryConflict(ValueError): pass


def platform_states(story, state):
    saved = state.get("platforms", {})
    result = {}
    for name in PLATFORMS:
        row = dict(saved.get(name, {"status": state.get("status", "unreviewed"),
                                    "approved_revision": state.get("approved_revision")}))
        if row.get("status") == "approved" and (row.get("approved_revision") != story.get("revision") or story.get("merged_into")):
            row["status"] = "needs_review"
        result[name] = row
    return result


def aggregate(platforms):
    states = {value["status"] for value in platforms.values()}
    if "needs_review" in states: return "needs_review"
    if len(states) == 1: return next(iter(states))
    return "draft"



def templates(story):
    title, summary = story["title"], story.get("summary", "")
    sources = "\n".join(s["url"] for s in story.get("sources", [])) or story["url"]
    tags = " ".join(story.get("hashtags", [])[:3])
    text, _ = tweet(title, story["url"], story.get("hashtags", []))
    return {"youtube_title": title[:100], "youtube_description": f"{title}\n\n{summary}\n\nSources:\n{sources}"[:5000],
            "x_post": text, "tiktok_caption": f"{title}\n\n{tags}"[:2200], "narration": f"{title}\n\n{summary}"[:12000]}


def decorate(story, editorial):
    state = dict(editorial or {})
    state.setdefault("version", 0)
    state.setdefault("status", "unreviewed"); state.setdefault("bookmarked", False)
    state.setdefault("drafts", {}); state.setdefault("notes", "")
    if state["status"] == "approved" and state.get("approved_revision") != story.get("revision"):
        state["status"] = "needs_review"
    state["platforms"] = platform_states(story, state)
    state["status"] = aggregate(state["platforms"])
    count = len(story.get("sources", []))
    safe_links = all(safe_article_url(url) for url in [story.get('url')] + [s.get('url') for s in story.get('sources', [])])
    priority = min(count, 5) * 5 + (15 if story.get("disagreement") else 0) + (10 if story.get("change") == "updated" else 0)
    return dict(story, editorial=state, review_priority=priority, actionable_links_safe=safe_links,
        quality_notes=[f"{count} source links; independence has not been verified.",
                       "Provider credibility/importance scores are assessments, not fact checks.",
                       "Media reuse rights are unknown until reviewed."] + (["Conflicting or changed headlines need review."] if story.get("disagreement") else []) +
                       ([] if safe_links else ["Invalid historical source link retained for evidence. Opening, drafting, approval and export of unsafe links are blocked."]))


def query_sql(query):
    allowed = {"q", "entity", "desk", "source", "provider", "status", "bookmarked", "include_muted", "include_merged", "sort", "limit", "offset"}
    if set(query) - allowed or any(not isinstance(v, str) or len(v) > 300 for v in query.values()):
        raise ValueError("invalid library filter")
    limit, offset = int(query.get("limit", "50")), int(query.get("offset", "0"))
    if not 1 <= limit <= 100 or not 0 <= offset <= 100000: raise ValueError("invalid library pagination")
    if query.get("sort", "recent") not in ("recent", "priority"): raise ValueError("invalid sort")
    if any(query.get(k, "false") not in ("true", "false") for k in ("bookmarked", "include_muted", "include_merged")):
        raise ValueError("boolean filters must be true or false")
    clauses, values = ["1=1"], []
    if query.get("include_merged") != "true": clauses.append("json_extract(s.payload,'$.merged_into') IS NULL")
    for key in ("q", "entity"):
        if query.get(key):
            clauses.append("instr(lower(coalesce(json_extract(s.payload,'$.title'),'') || ' ' || coalesce(json_extract(s.payload,'$.summary'),'') || ' ' || coalesce(json_extract(s.payload,'$.sources'),'')),lower(?))>0")
            values.append(query[key])
    if query.get("desk"):
        clauses.append("EXISTS(SELECT 1 FROM json_each(coalesce(json_extract(s.payload,'$.desks'),json_array(json_extract(s.payload,'$.desk')))) WHERE value=?)")
        values.append(query["desk"])
    if query.get("source"):
        clauses.append("EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src WHERE instr(lower(json_extract(src.value,'$.source')),lower(?))>0 OR instr(lower(json_extract(src.value,'$.url')),lower(?))>0)")
        values.extend([query["source"]] * 2)
    if query.get("provider"):
        clauses.append("EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src, json_each(src.value,'$.providers') p WHERE p.value=?)")
        values.append(query["provider"])
    if query.get("bookmarked") == "true": clauses.append("coalesce(json_extract(e.payload,'$.bookmarked'),0)=1")
    status = "CASE WHEN (json_extract(e.payload,'$.status')='approved' AND coalesce(json_extract(e.payload,'$.approved_revision'),0)!=json_extract(s.payload,'$.revision')) OR EXISTS(SELECT 1 FROM json_each(e.payload,'$.platforms') p WHERE json_extract(p.value,'$.status')='approved' AND coalesce(json_extract(p.value,'$.approved_revision'),0)!=json_extract(s.payload,'$.revision')) THEN 'needs_review' ELSE coalesce(json_extract(e.payload,'$.status'),'unreviewed') END"
    if query.get("status"):
        if query["status"] not in STATES: raise ValueError("invalid editorial status")
        clauses.append(status + "=?"); values.append(query["status"])
    if query.get("include_muted") != "true":
        clauses.append("(NOT EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources')) OR EXISTS(SELECT 1 FROM json_each(s.payload,'$.sources') src WHERE NOT EXISTS(SELECT 1 FROM library_preferences p WHERE p.kind='mute' AND lower(p.name)=lower(json_extract(src.value,'$.source')))))")
    priority = "min(coalesce(json_array_length(json_extract(s.payload,'$.sources')),0),5)*5 + CASE WHEN json_extract(s.payload,'$.disagreement')=1 THEN 15 ELSE 0 END + CASE WHEN json_extract(s.payload,'$.change')='updated' THEN 10 ELSE 0 END"
    order = (priority + " DESC," if query.get("sort") == "priority" else "") + "coalesce(json_extract(s.payload,'$.event_seq'),0) DESC,s.id"
    sql = "SELECT s.payload,coalesce(e.payload,'{}') FROM stories s LEFT JOIN editorial e ON e.story_id=s.id WHERE " + " AND ".join(clauses) + " ORDER BY " + order + " LIMIT ? OFFSET ?"
    return sql, values + [limit + 1, offset], limit, offset


class LibraryMixin:
    def library(self, query=None):
        sql, values, limit, offset = query_sql(query or {})
        with self.lock, self.connect() as db: rows = db.execute(sql, values).fetchall()
        return {"stories": [decorate(json.loads(a), json.loads(b)) for a, b in rows[:limit]],
                "offset": offset, "limit": limit, "next_offset": offset + limit if len(rows) > limit else None}

    def editorial(self, story_id):
        with self.lock, self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT payload,id FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", (story_id, story_id)).fetchone()
            if not row: return None
            state = db.execute("SELECT payload FROM editorial WHERE story_id=?", (row[1],)).fetchone()
            return decorate(json.loads(row[0]), json.loads(state[0]) if state else {})

    def save_editorial(self, story_id, change):
        if set(change) - {"expected_revision", "expected_editorial_version", "bookmarked", "status", "notes", "drafts", "draft_patch", "platform_status", "generate"}: raise ValueError("unknown editorial field")
        if "platform_status" in change and "status" in change: raise ValueError("choose platform_status or whole-package status")
        if sum(("drafts" in change, "draft_patch" in change, change.get("generate") is True)) > 1: raise ValueError("choose one draft operation")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,id FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", (story_id, story_id)).fetchone()
            if not row: raise LookupError("story not found")
            story, sid = json.loads(row[0]), row[1]
            if story.get("merged_into"): raise LibraryConflict("story was merged; edit its destination instead")
            if type(change.get("expected_revision")) is not int or change["expected_revision"] != story["revision"]: raise LibraryConflict("story changed; reload before saving")
            raw = db.execute("SELECT payload FROM editorial WHERE story_id=?", (sid,)).fetchone()
            state = decorate(story, json.loads(raw[0]) if raw else {})["editorial"]
            if type(change.get("expected_editorial_version")) is not int or change["expected_editorial_version"] != state["version"]: raise LibraryConflict("editorial changes exist; reload before saving")
            if "generate" in change and type(change["generate"]) is not bool: raise ValueError("generate must be boolean")
            if "bookmarked" in change:
                if type(change["bookmarked"]) is not bool: raise ValueError("bookmarked must be boolean")
                state["bookmarked"] = change["bookmarked"]
            if "notes" in change:
                if not isinstance(change["notes"], str) or len(change["notes"]) > 12000: raise ValueError("notes too long")
                state["notes"] = change["notes"]
            if change.get("generate") is True or "drafts" in change or "draft_patch" in change:
                require_safe_story(story)
                previous_drafts = state["drafts"]
                if "draft_patch" in change:
                    patch = change["draft_patch"]
                    if not isinstance(patch, dict) or not patch or set(patch)-set(FIELDS): raise ValueError("invalid draft patch")
                    drafts = {k: previous_drafts.get(k, "") for k in FIELDS}
                    drafts.update(patch)
                else: drafts = templates(story) if change.get("generate") is True else change["drafts"]
                if not isinstance(drafts, dict) or set(drafts) != set(FIELDS): raise ValueError("all five draft fields are required")
                if any(not isinstance(drafts[k], str) or len(drafts[k]) > length for k, length in FIELDS.items()): raise ValueError("draft field too long")
                if drafts["x_post"] and not measure(drafts["x_post"])["valid"]: raise ValueError("X draft is invalid or over 280 weighted characters")
                for name, fields in PLATFORMS.items():
                    if change.get("generate") is True or any(drafts[k] != previous_drafts.get(k, "") for k in fields):
                        state["platforms"][name] = {"status": "draft", "approved_revision": None}
                state.update(drafts=drafts, source_revision=story["revision"])
                state["status"] = aggregate(state["platforms"])
                if state["status"] != "approved": state["approved_revision"] = None
            if "status" in change:
                status = change["status"]
                if not isinstance(status, str) or status not in STATES: raise ValueError("invalid editorial status")
                if status == "approved":
                    require_safe_story(story)
                    if state.get("source_revision") != story["revision"]: raise LibraryConflict("drafts need review against the current revision")
                    if not all(state.get("drafts", {}).get(k, "").strip() for k in FIELDS): raise ValueError("complete the platform drafts before approval")
                    state["approved_revision"] = story["revision"]
                else: state["approved_revision"] = None
                state["status"] = status
                state["platforms"] = {name: {"status": status, "approved_revision": state["approved_revision"]} for name in PLATFORMS}
            if "platform_status" in change:
                decisions = change["platform_status"]
                if not isinstance(decisions, dict) or not decisions or set(decisions)-set(PLATFORMS): raise ValueError("invalid platform decision")
                for name, status in decisions.items():
                    if not isinstance(status, str) or status not in STATES: raise ValueError("invalid platform status")
                    if status == "approved":
                        require_safe_story(story)
                        if not all(state["drafts"].get(k, "").strip() for k in PLATFORMS[name]): raise ValueError("complete this platform's draft before approval")
                        if name == "x" and not measure(state["drafts"]["x_post"])["valid"]: raise ValueError("X draft is invalid or over 280 weighted characters")
                    state["platforms"][name] = {"status": status, "approved_revision": story["revision"] if status == "approved" else None}
                state["status"] = aggregate(state["platforms"])
                state["approved_revision"] = story["revision"] if state["status"] == "approved" else None
                if state["status"] == "approved": state["source_revision"] = story["revision"]
            state["version"] += 1
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT OR REPLACE INTO editorial VALUES (?,?)", (sid, json.dumps(state)))
            return decorate(story, state)

    def library_settings(self, kind=None, name=None, value=None, write=False):
        with self.lock, self.connect() as db:
            if write:
                if kind not in ("search", "mute") or not isinstance(name, str) or not 1 <= len(name.strip()) <= 200: raise ValueError("invalid preference")
                name = name.strip()
                if value is None: db.execute("DELETE FROM library_preferences WHERE kind=? AND name=?", (kind, name))
                else:
                    if kind == "search":
                        if not isinstance(value, dict): raise ValueError("search must be filters")
                        query_sql(value)
                    elif value is not True: raise ValueError("mute must be true or null")
                    db.execute("INSERT OR REPLACE INTO library_preferences VALUES (?,?,?)", (kind, name, json.dumps(value)))
            return {"preferences": [{"kind": k, "name": n, "value": json.loads(v)} for k, n, v in db.execute("SELECT kind,name,payload FROM library_preferences ORDER BY kind,name")]}

    def research_context(self, story_id):
        # Pin all three reads to one SQLite snapshot, including other processes.
        with self.lock, self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT payload,id FROM stories WHERE id=COALESCE((SELECT id FROM stories WHERE id=?),(SELECT story_id FROM identity_aliases WHERE alias=?))", (story_id, story_id)).fetchone()
            if not row: raise LookupError("story not found")
            state = db.execute("SELECT payload FROM editorial WHERE story_id=?", (row[1],)).fetchone()
            events = db.execute("SELECT payload FROM events WHERE story_id=? ORDER BY seq DESC LIMIT 2", (row[1],)).fetchall()
            return (decorate(json.loads(row[0]), json.loads(state[0]) if state else {}),
                    json.loads(events[1][0]) if len(events) > 1 else None)

    def research_brief(self, story_id):
        from .briefs import build_brief
        story, previous = self.research_context(story_id)
        return build_brief(story, previous)

    def research_export(self, story_id):
        from .briefs import build_brief
        story, previous = self.research_context(story_id)
        require_safe_story(story)
        if story.get("merged_into"): raise LibraryConflict("story was merged; export its destination instead")
        urls = list(dict.fromkeys(s["url"] for s in story.get("sources", []))) or [story["url"]]
        return {"schema_version": 1, "kind": "agent-news-bot-research", "story": story,
                "approved": story["editorial"]["status"] == "approved", "source_urls": urls,
                "platform_approvals": story["editorial"]["platforms"], "brief": build_brief(story, previous),
                "videobot": {"action": "inspect", "urls": urls,
                    "instructions": "Paste these source URLs into VideoBot Inspect URLs, review discovered media, then choose what to queue. No media availability or reuse permission is implied."},
                "rights": "unknown; review each source before reuse", "publishing": "manual only"}


def write_package(payload, parent):
    require_safe_story(payload['story'])
    if not all(safe_article_url(url) for url in payload['source_urls']): raise ValueError('invalid source URL')
    from pathlib import Path
    from uuid import uuid4
    import shutil
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    token = str(uuid4())
    staging, destination = parent / (".research-" + token), parent / ("Research-" + token)
    staging.mkdir(mode=0o700)
    try:
        story = payload["story"]; state = story["editorial"]; drafts = state["drafts"]
        parts = ["# " + story["title"], "Editorial state: " + state["status"]]
        parts += ["Platform approval: " + name + " — " + state["platforms"][name]["status"] for name in PLATFORMS]
        for key in sorted(FIELDS): parts += ["\n## " + key, drafts.get(key, "")]
        parts += ["\n## Research notes", state["notes"], "\n## Sources", "\n".join(payload["source_urls"]),
                  "\nMedia rights remain unknown. Paste sources.txt into VideoBot Inspect URLs, review discovered media, then choose what to queue. No downloading or posting was performed."]
        files = {"brief.md": payload["brief"]["markdown"], "research.json": json.dumps(payload, indent=2, ensure_ascii=False), "sources.txt": "\n".join(payload["source_urls"]) + "\n", "posts.md": "\n\n".join(parts)}
        for name, text in files.items():
            path = staging / name
            path.write_text(text, encoding="utf-8"); path.chmod(0o600)
        staging.rename(destination)
        return destination
    except Exception:
        shutil.rmtree(staging)
        raise
