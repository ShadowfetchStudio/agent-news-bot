# Release requirement and evidence map

The historical four-perspective audits are preserved under `audits/2026-11-27/`.
This map ties each numbered defect to tests of its repaired behavior. Test names
below are in `tests/test_worldmonitor.py` unless a different file is specified.
The verified operator commit is `75f03a7`; its CI is
[run 46048012868](https://github.com/ShadowfetchStudio/agent-news-bot/actions/runs/46048012868).
Release packaging changes add source provenance and distribution files; final
package identity is recorded by the release's `RELEASE-INFO.json`.

| Finding | Repaired behavior and regression evidence |
|---|---|
| B01 | Disconnects do not kill the native listener: `test_native_disconnect_does_not_kill_bridge`. |
| B02 | Repeated restart and occupied-port preservation: `test_native_listener_restarts_and_failed_port_change`. |
| B03 | Late context produces delivered enrichment without score churn: `test_enrichment_delivered_and_score_churn_stays_quiet`, `test_late_provider_enrichment_is_a_distinct_event_type`. |
| B04 | New provider correction survives stale direct reporting: `test_newer_provider_correction_does_not_revert_to_unchanged_rss`. |
| B05 | Cached unchanged feeds recover cap/lookback changes: `test_rss_cached_document_recovers_cap_and_lookback`. |
| B06 | Market checkpoints match selected observations: `test_market_checkpoint_matches_selected_symbol`, `test_swift_market_checkpoint_and_skill_preservation`. |
| B07 | Running/failed/completed fetch jobs are distinct: `test_fetch_job_truthful_completion`. |
| B08 | CLI finds installed state outside checkout: `tests/test_installed_cli.py`; actual native service script `tests/manual/native_install_smoke.py`. |
| B09 | Shared canonical identity and preserved aliases/history: `test_canonical_identity_contract`, `test_identity_upgrade_keeps_history_and_old_story_id`. |
| B10 | Empty, partial and failed coverage are distinct: `test_all_failed_fetch_cannot_claim_success`, `test_coverage_state_parity`. |
| B11 | Unknown dates and restored snapshots expire without renewing first-seen: `test_undated_stories_expire_and_carry_counts_are_explicit`, `test_native_snapshot_restores_without_renewing_dates_or_coverage`. |
| B12 | Official weighted X rules, Unicode and URL parity: `test_official_x_weighted_conformance`, `test_native_x_weighted_conformance`, composer tests. |
| B13 | Custom agent skills survive explicit managed install: `test_swift_market_checkpoint_and_skill_preservation`; startup no longer writes skills. |
| B14 | New/updated/carried/displayed counts agree: `test_native_separate_change_and_display_counts`, undated/carry count test. |
| R01 | Private stable credentials, failed writes and rotation: `test_keychain_errors_do_not_erase_or_hide_credentials`, `test_native_private_credential_failures_concurrency_and_rotation`, `test_native_token_rotation_updates_running_listener`; final native UI failure/reload evidence. |
| R02 | Compatible discovery and nonempty completed model reply required: `test_agent_readiness_requires_compatible_model_list`, `test_agent_invalid_and_incomplete_completion_is_not_success`, explicit reply tests. Controlled services, not live model acceptance. |
| R03 | Distinct events remain distinct; manual repair is atomic and replay-safe: `test_different_events_do_not_merge_by_word_overlap`, split/merge/stale/conflict/rollback tests. |
| R04 | Non-web links rejected at ingestion and action/export boundaries: `test_actionable_url_policy_parity`, unsafe RSS and legacy export/snapshot tests. |
| R05 | Per-desk caps survive carry-over and shared membership: `test_independent_desk_caps_preserve_shared_membership`. |
| R06 | Bounded clients, headers, bodies and slow readers: `tests/test_http_bounds.py`. |
| R07 | Non-ASCII credentials return 401 without terminating service: `test_non_ascii_auth_returns_401_and_server_recovers` in that file. |
| R08 | Direct/World Monitor decoded-byte and total-time limits, worker reap: direct stream/deadline tests and World Monitor trickling-worker test. |
| R09 | Failed snapshot replacement preserves the old copy, with visible retry: `test_native_snapshot_atomic_failure_preservation_and_retry`, invalid-destination tests and recorded native UI evidence. |

## Additional capability requirements

| Requirement | Evidence |
|---|---|
| Setup, optional provider diagnostics | Setup migration, HTTP status/schema/transport/redirect tests; native setup walkthrough recorded in checklist. |
| Refresh, restored batch, multiple desks, timeline | `release-native-ui.json`: expired item removed, shared story in both desks, two actual five-minute refreshes, provider differences and revision details visible. |
| Library, bookmarks, mutes, briefs and platform review | Library/brief tests in `test_worldmonitor.py`, `test_platform_approval.py`, recorded native UI walkthroughs. |
| Saved-search alerts/review queue | `test_alerts.py`; `alerts-native-ui.json`; bounded 32-subscription/6,400-match trial. |
| Durable automation and explicit outbox | Consumer contract tests, `test_reference_consumer.py`, `examples/reliable_consumer.py` and consumer guide. |
| Source health, retries, operators | `test_source_health.py`, `test_storage.py`, `operator-native-ui.json`; live source observations are dated, not permanent uptime claims. |
| Research/config/export recovery and safe retention | `test_recovery.py`, `test_application_recovery.py`, `test_retention.py`; archived load trials and private/failed-restore cases. |
| Installation, permissions, dependency locks | `test_installed_cli.py`, `test_private_config.py`, private database/export tests, actual native user-service smoke script, locked dependency CI. |
| Original icon and distribution | `Resources/ICON.md`, Mac ICNS build, installed Linux resource, `tools/package_release.py`, release manifest/checksums. |
| Documentation and honest release claims | README, versioned API/consumer/source/rights/recovery guides, `RELEASE_NOTES_1.2.0.md`, `MAC_INSTALL.md`; final live GitHub assets checked separately. |

This is an evidence map, not a claim of exhaustive proof across all operating
systems, providers or assistive technologies. The release notes explicitly retain
unverified VoiceOver speech, actual optional agent/model/provider accounts, native
Intel/root installation and long-duration production limits. The owner authorized
the explicitly labeled ad-hoc, non-notarized Mac distribution.
