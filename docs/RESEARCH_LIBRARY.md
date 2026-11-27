# Research library and editorial workflow

The persistent library reads the story journal, independently of the current fresh batch. On macOS, choose **Library / drafts** in the toolbar. On Linux, use the CLI or authenticated localhost API below. Neither interface publishes posts or queues media downloads.

## Find and review

Search title, summary and source observations; filter by desk, provider, source name/URL, editorial state or bookmark. The entity/location box is a keyword search across retained text and provider observations, not automatic named-entity extraction. SQLite's built-in lowercasing is ASCII-oriented: non-ASCII text can be searched literally but full Unicode case folding is not promised. Queries do not fetch full publisher articles.

Save the current filters as a named watchlist. Muting a source hides a story only when every source in that group is muted. Mixed-source stories retain all their evidence. **Mutes, filters, bookmarks and editorial changes do not discard journal events or advance agent consumer cursors.** Choose Include muted sources or unmute from the menu to recover hidden rows.

Default ordering is newest recorded revision. Review priority is a transparent heuristic: five points per source link (capped at five links), fifteen for flagged disagreement, and ten for a correction. It is not a credibility, independence or truth score. This is story ordering, not an automatically researched briefing.

Select a story and choose **Open story and drafts**. Story shows the supplied summary and original HTTP(S) links; History exposes retained revisions. Quality notes distinguish provider assessments, unknown media rights and unverified source independence. The library reloads when the editor closes.

## Draft and approve

Generate templates creates five editable fields: YouTube title/description, X post, TikTok caption, and a narration starter. These use retained story text and source links; they are not completed reporting or a finished video script. Develop and fact-check them before recording or posting.

Save uses both the current story revision and editorial version. If another writer saved a draft or the story changed, it refuses to overwrite and preserves the open editor text. Copy unsaved work before reloading. Done and Reload ask before discarding edits; the editor labels unsaved changes. Regenerate explicitly confirms replacing templates.

Approval requires all five fields, valid draft limits and a current source revision. A later story revision changes the effective state to **needs review**. Approval is a local editorial decision, not a claim of factual or licensing clearance. Refresh/reopen to see an external correction; an old open editor cannot save over the new revision.

X uses the packaged twitter-text weighted parser and a 280-character ceiling; the editor shows its live weighted count. Other enforced limits are 100 Unicode code points for YouTube title, 5,000 for description, 2,200 for TikTok caption, and 12,000 for narration/notes. These are this app's conservative draft constraints, not promises about every platform/API's current limits. Invalid changes roll back entirely.

## Research export and VideoBot

**Export saved research package** exports the last saved version, not unsaved editor contents. Each export creates its own `Research-<UUID>` directory containing:

- `research.json`: current story, provenance, editorial state, revision and source URLs.
- `posts.md`: saved platform drafts, narration starter, notes and rights reminders.
- `sources.txt`: one original source URL per line.

Directories are private (0700), files 0600, and an atomic staged rename avoids half-complete packages and overwriting earlier exports. The JSON records whether approval still matches the current revision. Exports may contain private editorial notes; share deliberately.

VideoBot's supported handoff is **Inspect URLs**: paste `sources.txt`, inspect discovered media and select what to queue. Article URLs may contain no downloadable media. This is not a native VideoBot project importer, automatic queue submission or media-rights clearance. The integration follows the inspected [VideoBot workflow](https://github.com/ShadowfetchStudio/videobot#readme).

## Authenticated localhost API

Use the same bearer token as other bridge routes. All JSON responses are objects.

| Route | Method | Contract |
|---|---|---|
| `/v1/library` | GET | `q`, `entity`, `desk`, `source`, `provider`, `status`, `bookmarked`, `include_muted`, `include_merged`, `sort`, `limit`, `offset` |
| `/v1/library/settings` | GET | Saved preferences under `preferences` |
| `/v1/library/settings` | POST | `{kind: "search" or "mute", name, value}`; null deletes; searches store filter objects; mutes use true |
| `/v1/library/editorial?id=ID` | GET | `story` object with `editorial`, `review_priority`, `quality_notes` |
| `/v1/library/editorial?id=ID` | POST | Revision-bound change object below; returns updated `story` |
| `/v1/library/export?id=ID` | GET | Research JSON payload; does not write server files |

Boolean query values must be `true`/`false`. Sort is `recent`/`priority`. Limit is 1–100 (default 50), offset 0–100000. Responses include `next_offset` or null. Offset pagination is for interactive browsing and may shift during ingestion; durable agent delivery uses the event cursor instead. Unknown query fields are rejected.

Editorial changes must include integer `expected_revision` and `expected_editorial_version`. Optional fields: boolean `bookmarked`, `status`, string `notes`, `generate: true`, or `drafts` containing all five keys (`youtube_title`, `youtube_description`, `x_post`, `tiktok_caption`, `narration`). Editing drafts resets approval unless explicitly approving the same current revision. A bookmark/notes-only API change preserves existing approval.

Requests require Content-Length, a JSON object and at most 64 KiB; chunked transfer is not supported. Authentication precedes body parsing. Errors: 400 invalid input, 401 unauthorized, 404 story missing, 405 wrong method, 409 stale revision/editorial version, 503 storage unavailable. Only a successful save increments the editorial version.

## Linux CLI

Set `AGENT_NEWSBOT_HOME` to the service's data folder when it differs from the default. Commands fail without creating an empty journal if no fetch has populated it yet.

```sh
agent-newsbot library --entity NASA --desk science --sort priority
agent-newsbot library --bookmarked --limit 25
agent-newsbot editorial STORY_ID
agent-newsbot editorial STORY_ID --changes change.json
agent-newsbot preferences
agent-newsbot preferences --changes watchlist.json
agent-newsbot export-research STORY_ID --destination ./research
```

Example `change.json`, using versions obtained from the preceding editorial read:

```json
{"expected_revision":1,"expected_editorial_version":0,"generate":true,"bookmarked":true}
```

Example `watchlist.json`:

```json
{"kind":"search","name":"Space research","value":{"entity":"NASA","desk":"science"}}
```

The CLI reads/writes the same SQLite journal as the service and applies the same concurrency and validation rules. It can run from outside the checkout when installed.

## Manual grouping repairs

Select **Repair grouping…** to split source reports or merge into a previewed destination. **Include merged history** exposes retained old IDs and drafts; they cannot be exported as active stories. The Story tab shows IDs, repair reasons and related IDs. See [the complete repair contract, API and CLI](GROUP_REPAIRS.md).
