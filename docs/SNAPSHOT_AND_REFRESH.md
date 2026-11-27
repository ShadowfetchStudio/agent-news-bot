# Display snapshots and refresh

The Mac app restores `Exports/latest.json` at startup. It rejects snapshots over 16 MiB and reports unreadable data without altering the story journal. Linux restores its `shared/latest.json` with the same size ceiling.

Restored rows are **carried**, never newly delivered. Publication time, then immutable `first_seen`, determines eligibility within the selected lookback window. Legacy undated rows use the original snapshot time once; that value is preserved as first-seen and is never replaced with the restart time. Rows more than five minutes in the future are excluded. A restore cannot create a journal event or acknowledge an agent's cursor.

Successful/partial source checks from a restored snapshot become stale, retaining their original check times. A previous failed check remains unavailable. A new refresh replaces coverage with the actual new check results. Coverage remains a report of the last attempt, not a continuous uptime monitor.

Both platforms retain other eligible stories when a new report arrives in the same desk. A new revision replaces the old displayed copy by stable story ID. The display is bounded to `max_per_desk × enabled_desk_count` rows, newest first; the full revision history remains in SQLite. Desk memberships can overlap. Changed-story counts and carried/displayed counts remain distinct. `/v1/status.fresh` excludes carried rows, while `displayed` includes them.

## Mac refresh controls

Settings → Freshness controls automatic refresh while the app is running (enabled by default, every 15 minutes; configurable from 5–120 minutes). After first-run setup is complete, the first refresh begins after a two-second startup grace period. The interval runs after the previous attempt finishes. Fetch Fresh and HTTP jobs share the existing busy gate, so attempts do not overlap. Reopening the window does not initialize another engine or refresh loop. Closing Settings reapplies the schedule. Automatic refresh does not install a background login service; quitting the app stops it.

The selected story remains selected if it is still in the refreshed display. No automatic agent handoff or posting is performed.

## Isolated native testing

Launching the executable with `AGENT_NEWSBOT_HOME=/absolute/test/path` redirects app data and exports. It also derives a separate UserDefaults suite and Keychain service from that path. Use a new test directory and a separate bridge port. The normal app's saved data, preferences and credential service are not used. This is for development/test instances; moving the test path intentionally creates a different preference/credential namespace.

The snapshot policy is exercised in the native integration harness with fixed-time fixtures: expiry, future dates, immutable unknown-date fallback, carried counts, stale coverage, same-ID correction replacement and retention alongside new stories. The final native walkthrough verified restoration, shared desks and two actual five-minute refreshes; see [release evidence](audits/2026-11-27/release-native-ui.json). Months of unattended operation remain outside this validation.
