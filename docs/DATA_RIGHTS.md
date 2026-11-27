# Code, hosted outputs and source rights

Reviewed 2026-11-27. This document describes integration boundaries and the app's behavior; it does not certify an account, deployment or publication as licensed.

## Separate permissions

Agent News Bot's own code is [MIT licensed](../LICENSE). Its World Monitor adapter is independently implemented against the digest REST contract; it does not package the World Monitor server/dashboard. Bundled dependencies retain their own notices.

World Monitor's [licensing page](https://github.com/koala73/worldmonitor/blob/main/docs/license.mdx) identifies its platform as AGPL-3.0-only and its thin clients as MIT. Neither code license supplies rights to its hosted outputs or branding. Self-hosting and calling its hosted servers are separate choices.

The [hosted EULA](https://github.com/koala73/worldmonitor/blob/main/docs/eula.mdx), dated November 2, 2026, distinguishes personal research, organizational use and customer-facing distribution. It gives different rules to derived facts, structured events, attributed headline/snippet content and full source content. Its R3 headline/snippet category has a 30-day retention period; R4 full source content is internal-only during an active plan. Plan scope and source-specific restrictions still apply. A signed agreement can differ. An agent consumes the operator's permissions; automation does not expand them. Check the current terms and the actual account agreement before selecting a hosted endpoint.

These statements describe those published documents as observed on the review date. They are not a perpetual representation of upstream terms or a determination of which output category a particular payload belongs to.

## What Agent News Bot actually stores

The research journal persists current titles/summaries, source/provider observations and revision events. Other copies can exist in snapshots, research exports, backup archives and downstream consumer inboxes. Source-health records and alert metadata also persist locally. The app does not infer the legal classification or expiry of each incoming field.

The [history maintenance workflow](HISTORY_RETENTION.md) is designed to preserve research correctness and consumer delivery. It protects current/latest revisions, unread notifications, bookmarks, draft/approval evidence and unacknowledged events. It does **not** enforce a provider retention deadline, remove current story text, find every exported copy, or revoke downstream data. Archiving an event is not destruction of that content: the required backup retains it.

Consequently, the standard persistent research workflow is **not a hosted-output retention compliance mechanism**. Do not configure content whose required retention/deletion policy conflicts with this behavior. Use sources and an endpoint agreement that permit the intended storage/export/agent workflow, or keep that connector disabled. A self-hosted endpoint avoids assuming hosted-service access rights; it does not grant rights to its upstream publishers' content.

## Before sharing an export or making a video

- Follow each source link and review the reporting, relevant date, attribution and reuse terms. Provider scores and multiple links are not factual verification.
- Treat source excerpts, images, audio and full articles separately from independently written commentary. This app does not acquire media licenses or clear an image for a thumbnail.
- Keep required source attribution and restrictions when moving material to VideoBot or another agent. The local HTTP bridge and consumer acknowledgement do not authorize external redistribution.
- Platform approval records a local editorial decision against a story revision. It is not legal clearance or authorization for this app to publish; the app has no automatic social publisher.
- Retain the applicable agreement/permissions outside the research export when needed. Do not put account keys or license credentials into public packages.

Direct RSS/API availability alone is not a reuse license. Research exports carry source URLs and uncertainty notes, but they do not contain a complete rights ledger for every upstream provider. The final publication decision remains with the operator and the applicable permissions.
