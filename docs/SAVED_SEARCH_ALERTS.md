# Saved-search alerts and correction review

Saved searches can now become **local notification subscriptions**. An enabled subscription records future matching story revisions in a persistent inbox. The Mac toolbar shows unread count, and the inbox refreshes while open. Linux and other local clients use the authenticated API or CLI. There are no email, push-service, OS notification permission, external transmission or automatic publishing actions.

## Native workflow

1. Open **Research library**, choose search/entity/desk/source/provider/state/bookmark/mute filters and enter a watchlist name. **Save filters** saves the ordinary reusable search; **Watch for changes** additionally creates or updates an alert with that name.
2. Alerts apply to active stories. Turn off **Include merged history** before enabling a watch. Sort and pagination are presentation settings and are not subscription filters.
3. Use the toolbar bell/count or **Alerts inbox**. The inbox shows the matched revision, the story's current revision and the subscription version. If the story changed again after a match, review its current report and retained history. A matched title displayed in the inbox is the current retained title, not a claim that an older headline still applies.
4. **Open current story** opens its editorial details (or its merge destination). **Mark seen** acknowledges only that inbox item. It does not approve anything, change notes/drafts or advance an agent delivery cursor. A newer match arriving before the click is committed causes a conflict and requires reloading.
5. **Pause** leaves the inbox intact and stops new notifications. **Resume** starts with future revisions; it does not backfill the paused interval. Changing filters likewise applies only to future revisions. The app or service must be running and ingesting reports; closing it does not schedule background OS notifications. Existing inbox rows retain the filter version that matched them.
6. **Remove** explicitly removes a subscription and its inbox metadata after the app's confirmation; research, drafts, history and agent checkpoints stay. The separate ordinary saved search is unchanged. Deleting saved filters likewise does not silently remove their independent alert.

**Review queue** is a separate Library shortcut: all active stories whose aggregate or platform approval now needs review, ordered by review priority, including muted sources. It resets the search filters so other criteria cannot hide changed approved drafts. Marking an alert seen does not remove a story from this queue. Explicit editorial review/reapproval updates the revision-bound decision. This queue is derived from durable approval state and source revisions, so it survives restart without a second copy of editorial truth.

## Matching and delivery scope

Matching uses the same library SQL predicates, without library pagination. Up to 32 named subscriptions are supported; names are 1–80 characters. Allowed filters are `q`, `entity`, `desk`, `source`, `provider`, `status`, `bookmarked` and `include_muted`, with the same string values as Library queries. Empty filters watch all active stories. Status/bookmark/mute filters are evaluated when a new source revision or grouping-repair event is recorded. Merely editing a draft, changing preferences or enabling a subscription does not create a retrospective notification.

Each subscription/story pair has one inbox row. Later matching revisions update that row and make it unread again; already-seen revisions are not re-emitted on restart. Intermediate matching revisions are **coalesced**, not delivered individually. The research journal remains the event history. Agents requiring every event must use their independent durable consumer stream, not this inbox.

Story/event writes and matching inbox updates share one transaction in both runtimes. A failed inbox write prevents that ingestion/repair transaction from committing. Independent app/CLI writers are serialized by SQLite, and version checks reject stale subscription edits. A durable monotonically increasing subscription clock prevents an old editor from overwriting a deleted-and-recreated name.

Subscriptions, inbox read positions and their clock are private journal data, included in research backup/restore. Old backups without alert tables remain readable; the current app initializes empty alert state. Unread matches protect their referenced event from history pruning, even when subsequent corrections no longer match. Marking them seen releases that additional retention protection; bookmarks, latest revisions, drafts/approvals and agent checkpoints retain their independent protections. Deleting a subscription removes its unread retention holds.

Inbox metadata grows with matched stories, not with repeated revisions of the same story. There is no automatic inbox deletion or backfill. Removing an unused subscription clears its inbox metadata; overall journal/storage policies remain separate. Reading an old backup can restore earlier unread/seen state just as it can restore earlier agent checkpoints. Reconnect with the restored service's new bridge credential and reload subscription versions; versions are not a global clock across independent/restored journals. All writers to an alerts-enabled journal must run this release: older writers cannot generate these notifications.

## Authenticated API

Capability: `saved_search_alerts: 1`. Route: `GET` or `POST /v1/library/alerts`.

GET parameters: `name` (optional exact subscription name), `unread` (`true` by default; `false` includes seen rows), `limit` (1–100, default 50) and `offset` (0–100000). Response fields:

- `subscriptions`: every current subscription with `name`, opaque integer `version`, `filters`, `enabled` and `start_seq`.
- `matches`: requested page of inbox rows, ordered by newest matching sequence then name/story ID. Each row has `name`, `story_id`, `seq`, `matched_revision`, `current_revision`, `subscription_version`, `unread`, current `title` and optional `merged_into`.
- `unread_count`: total unread rows across all subscriptions, independent of page filters.
- `limit`, `offset`, `next_offset`.

GET does not mark anything seen or modify a consumer. Offset pagination is a live view; new matches may move page boundaries. Use exact sequence/version fields when acting on a row, not a remembered list position.

POST requires a JSON object, normal local bridge authentication, no query parameters and the normal 64 KiB body limit:

```json
{"action":"subscribe","name":"Lunar launches","expected_version":0,"filters":{"q":"lunar","desk":"science"},"enabled":true}
```

Use version zero only for a name that does not exist. For update/pause/resume, send the current returned `expected_version`, complete filters and explicit Boolean `enabled`. Versions are opaque increasing integers; do not calculate the next value yourself. Every save establishes a new future-only start sequence. Reusing an existing name intentionally updates its alert; it does not create a duplicate.

```json
{"action":"mark_seen","name":"Lunar launches","story_id":"STORY_ID","expected_seq":123}
```

An identical seen request is idempotent while that same match remains current. A newer matching sequence returns 409; read again rather than automatically marking the new report seen.

```json
{"action":"delete","name":"Lunar launches","expected_version":7}
```

Invalid commands return 400, missing subscription/match 404, stale versions/sequence or subscription capacity 409, and storage failures 503. Unknown fields are rejected. After an ambiguous subscribe/delete response, read current state before retrying; do not assume the mutation failed. No response means no proof of rollback.

## Linux CLI

```sh
agent-newsbot alerts
agent-newsbot alerts --unread false --name "Lunar launches" --limit 50
agent-newsbot alerts --changes alert-command.json
agent-newsbot library --status needs_review --include-muted --sort priority
```

The change file uses the API's POST format and is limited to 64 KiB. Select the actual service home through `AGENT_NEWSBOT_HOME` when needed. The CLI does not create a missing research journal or contact a publisher/model to read alerts. Alert queries and mutations are also covered by the shared research recovery and history-retention tools.
