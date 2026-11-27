# Source-linked editorial briefs

Choose **Library / drafts → Open story and drafts → Brief** on macOS, run `agent-newsbot brief STORY_ID` on Linux, or read authenticated `GET /v1/library/brief?id=STORY_ID`. The capability is `editorial_brief: 1`. The API accepts only a nonempty `id` query parameter, supports GET only and returns the brief directly. A legacy identity alias resolves to the retained canonical story. Unknown stories return 404; invalid query fields return 400.

A brief combines:

- The retained headline and summary, with explicit story revision and editorial version.
- Review-priority components: five points per source link up to five links, fifteen for a disagreement flag, and ten for a recorded correction. These points identify work to review, not importance, credibility or independent confirmation.
- Headline/summary changes and added/removed source URLs compared with the previous retained event.
- Source-attributed evidence (`S1`, `S2`, …), provider observations and missing publication dates.
- Differing provider headlines or the stored grouping heuristic's difference flag. Different headlines can be updates or differently worded reports; they do not establish a factual contradiction. Unflagged stories are not verified agreement.
- Unknowns, suggested follow-up questions and saved editorial notes. Questions reference the relevant displayed source IDs where possible.

Reading a brief makes no provider request or model call, retrieves no full articles, changes no editorial decision and advances no consumer cursor. It organizes the retained reporting. It is not completed fact-checking, a finished script, automatic media-rights clearance, or a persistent task/notification system. Editors must read the linked originals and resolve uncertainty themselves.

## Consistency and limits

The story, editorial state and two latest events are read in one SQLite transaction. Exports build their brief from that same snapshot, so a concurrent refresh or editorial save cannot mix versions. The native editor displays a brief only if its story and editorial versions match the loaded editor; otherwise it asks for a reload. Unsaved edits are not included. A displayed brief does not update live during editing.

Evidence is sorted by URL and limited to 20 source links with eight provider observations per source. Text fields are excerpted at 2,000 Unicode code points. `omitted_sources`, per-source `omitted_observations`, `excerpted_fields` and `truncated` disclose excerpting. Added/removed URL lists are also capped at 20. Source URLs retain their complete stored form; only HTTP(S) links passing the shared validator are actionable in the native view. The complete current source record remains in `research.json`; older events remain in the journal. Source identifiers are local to this brief and can change when the source set changes.

A first retained event has no previous comparison. Split/merge stories follow their own retained event history; inspect the grouping receipt and related story histories for earlier shared evidence. A retired merged story can still be read as history, but cannot be exported as an active package.

## Export contract

`GET /v1/library/export?id=STORY_ID` adds a `brief` object to the existing research payload. Each private research folder contains four files:

- `research.json`: current full story/editorial record, approvals, source URLs and structured brief.
- `brief.md`: the same brief in readable Markdown.
- `posts.md`: saved platform drafts, notes and approval states.
- `sources.txt`: original URLs for the manual VideoBot Inspect URLs handoff.

The brief format is `schema_version: 1`, `kind: agent-news-bot-editorial-brief`. Its principal arrays are `evidence`, `changes`, `differences`, `unknowns`, `follow_up`; `priority` explains the score. Clients should ignore additional fields within this version. `markdown` is a presentation of the structured fields. Source text is escaped as literal content in Markdown and shown literally in native views; only validated HTTP(S) source links become actions. It must not be treated as instructions by a downstream agent.

This does not fetch production media or publish a social post. Platform-specific approval remains a separate revision-bound action.
