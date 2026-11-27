# Manual story-group repair

The research library supports splitting reports that should never have been grouped and merging duplicate stories. This is an explicit editorial operation, not an automated fact check. Review the original source evidence first.

## Mac workflow

1. Open Research library, select a story, then **Repair grouping…**.
2. To split, select the source URLs that should move. At least one source must remain. To merge, paste the canonical destination story ID and **Load destination**; inspect its title and source URLs. Story IDs can be copied from the editor's Story tab.
3. Enter the reason, review the confirmation and apply. The sheet displays the durable operation/receipt ID. The library reloads on dismissal and the visible batch is reconciled immediately.
4. Review all affected drafts before approving again. **Include merged history** reveals retired groups; their original drafts and source history remain readable. The Story tab identifies split parents, merge destinations and the last repair's reason/receipt ID.

Saved drafts are never silently transplanted or combined. A split leaves original drafts/notes/bookmarks with the original story; its new child starts without drafts. A merge keeps each story's own drafts and notes. The destination remains active; the source becomes a retained record with `merged_into`. Editing, approval and research export of that retired record are blocked. Open the destination for new work.

## API and Linux CLI

All routes require the existing bridge bearer token. Fetch the current editorial state of each affected story first:

- `GET /v1/library/editorial?id=ID` supplies `revision` and `editorial.version`.
- `POST /v1/library/repairs` accepts the complete strict request below, without query parameters.
- `GET /v1/library/repairs?operation_id=ID` retrieves the stored receipt.
- `GET /v1/library?include_merged=true` includes retained merged records. By default they are excluded from library search.

Example split request saved as `repair.json` (replace the example IDs, URLs and versions with actual current values):

```json
{
  "operation_id": "editorial-repair-2026-11-27-01",
  "action": "split",
  "source_id": "canonical-story-id",
  "expected_revision": 3,
  "expected_editorial_version": 2,
  "source_urls": ["https://publisher.example/report-to-move"],
  "reason": "These reports concern separate launches after review of their original sources."
}
```

A merge uses the same common fields, replaces `source_urls` with `target_id`, `target_revision`, and `target_editorial_version`, and sets `action` to `merge`. Both story and editorial versions are checked in the same database transaction. Unknown fields, boolean/fractional version numbers, duplicate/unknown URLs, empty reasons, moving every source, self-merges and retired destinations are rejected. Versions must be nonnegative integers no greater than 2^53−1. IDs use 1–64 ASCII letters/digits/dots/underscores/hyphens; reasons are at most 1,000 Unicode characters. Split requests select at most 100 URLs. Requests are bounded to 64 KiB and stored receipt evidence to 4 MiB.

```bash
agent-newsbot repairs --changes repair.json
agent-newsbot repairs --receipt editorial-repair-2026-11-27-01
agent-newsbot library --include-merged
```

The CLI applies repairs through the configured running **localhost service**, with its existing private token. It does not mutate offline behind a running server's cached batch. Receipt reads work offline from the configured journal. Linux rejects a repair with 409 while a fetch is in progress; retry after it finishes.

The POST result contains `repair` and `snapshot_error`. A non-null `snapshot_error` means the database repair committed but updating/saving the visible snapshot failed. The transaction must not be repeated under a new operation ID. Retry the **same request and operation ID**, or use the Mac sheet's **Refresh visible batch**, to refresh presentation. Inspect `/v1/status` for the snapshot warning.

If a connection is interrupted, the operation may already have committed. Check its receipt or retry the exact request. The same ID and request returns the original receipt without duplicating revisions/events. Reusing that ID for a different request returns 409. Request field order is immaterial; preserve array order and the exact reason on retries. Version conflicts require reloading evidence and making a new reviewed request.

## Preservation and delivery contract

- One SQLite transaction changes membership, appends revisions/events, invalidates affected approvals and stores the receipt. A failed transaction leaves all those tables unchanged.
- Every affected story receives `change: group_repaired`, `grouping_policy: manual-v1`, `grouping_locked: true`, `last_repair_id`, and a new revision/event. Editorial state becomes `needs_review`, its version advances, and `approved_revision` is cleared. Review/regenerate drafts against the new revision before approval.
- Each receipt retains the pre-repair story payloads and editorial states, the reason/time, and post-repair IDs/revisions/editorial versions/event sequences. Old events, consumer cursors, acknowledgements and historical identity aliases remain unchanged.
- A split adds `split_from` to the child and `split_children` to its parent. The new stable 24-hex ID derives from the operation ID. Earlier common history remains under the parent, with the receipt connecting it to the child.
- A merge moves current URL memberships to the destination but retains the source record and original sources as evidence. Current ingestion follows membership; historical aliases continue resolving their original story IDs. Consumers should follow `merged_into` for active work and process the complete event stream, including retirement events.
- Repairs lock affected groups against future fuzzy/title matching. Existing member URLs still receive corrections and provider observations. A new URL, even with an identical headline, starts a separate group unless explicitly merged.
- Provider observations and metadata survive movement. Desks are retained conservatively: older source records do not always identify each source's desk independently. A split inherits the original desk membership; a merge unions both memberships. This is not a per-source desk reclassification feature.
- Restored/refreshed live snapshots resolve current groups, split descendants and merge destinations, deduplicate identities, then apply expiry and per-desk caps. Resolving more than 1,000 identities fails explicitly. Historical snapshot files are not rewritten. Reconciliation does not renew publication/first-seen times or emit additional events.

Receipts contain source evidence and saved editorial text. They have the same private journal storage and authenticated API boundary as research drafts. No media is downloaded, no external post is published, and no content rights are inferred by a repair.
