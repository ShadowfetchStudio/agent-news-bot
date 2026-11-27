# Platform-specific editorial approval

You can save and approve an X post without completing the YouTube, TikTok or narration drafts. Each story now exposes `editorial.platforms` with independent `youtube`, `x`, `tiktok` and `narration` decisions. Each decision has a `status` and an `approved_revision` (null unless approved).

YouTube requires its title and description; X requires its post; TikTok requires its caption; narration requires its script. Existing length/weighted-X validation still applies. This is approval of saved text against a particular source revision, not permission to publish automatically, proof of factual accuracy or media-rights clearance.

## In the Mac editor

Each platform tab shows its saved approval state and provides **Save and approve** plus a menu for Draft, Needs review or Rejected. These actions save the editor's unsaved text/notes/bookmark changes and apply the explicit decision only to that platform. The tab warns when its text differs from the saved version.

The **Whole-package state** control still applies a decision to all four platforms. To avoid an ambiguous request, save or revert a changed whole-package state before using a platform button. Ordinary Save changes no longer resubmits an unchanged approval decision. Editing an X post clears X's approval; unchanged approvals for other platforms survive. Regenerating templates clears every platform approval even if some generated text happens to be identical.

When retained sources change to a new story revision, previously approved platform decisions become Needs review. The Library's Needs review filter includes those stories, even when only one platform had been approved. Re-approval is an explicit review of the saved text against the currently displayed revision. Group split/merge marks all affected platform drafts Needs review and clears approval revisions, including copied child drafts.

## API and CLI

Capability discovery advertises `platform_approval: 1`. The existing editorial endpoint accepts two additional fields:

```json
{
  "expected_revision": 3,
  "expected_editorial_version": 7,
  "draft_patch": {"x_post": "Your reviewed post and source link."},
  "platform_status": {"x": "approved"}
}
```

Send this object to `POST /v1/library/editorial?id=STORY_ID`, or save it as a file for `agent-newsbot editorial STORY_ID --changes change.json`. Both existing revision/version checks remain mandatory. A stale request returns 409; invalid decisions or missing required text return 400, without saving a partial change.

- `draft_patch` is a nonempty subset of the five existing draft fields. Unspecified fields are preserved, and initially missing fields become empty strings. The legacy `drafts` operation still accepts the complete five-field object.
- Choose exactly one of `draft_patch`, `drafts`, or `generate: true` for a draft operation.
- `platform_status` is a nonempty subset of the four platform names, with values `unreviewed`, `draft`, `needs_review`, `approved`, or `rejected`.
- Do not combine `platform_status` and the legacy whole-package `status` in one request.
- Notes/bookmarks do not revoke approvals. A no-op draft save preserves approvals. Changed fields revoke only their platform's approval; an explicit same-request decision can approve the resulting saved text.

The aggregate `editorial.status` remains compatible with package-oriented clients: it is Approved only when all four platforms are approved; Needs review if any platform needs review; a shared status when all four match; otherwise Draft. The top-level export `approved` still means the whole package is approved. For one-platform use, read `platform_approvals` and its revision rather than interpreting the aggregate flag as an X-only decision.

Existing records without platform decisions inherit their whole-package status and approval revision on read. They acquire the explicit platform map when next saved; no research history or prior decision is discarded during migration. `research.json` exports the per-platform decisions, and `posts.md` lists each saved platform state. Exports remain manual research packages, and creating one never posts anything.
