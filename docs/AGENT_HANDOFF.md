# Agent handoff safety and limits

Handoffs are explicit user actions. News retrieval, automatic refresh, startup and snapshot restoration do not send stories to agents or publish posts. The brief requests drafts only, treats source text as untrusted data, preserves source links and distinguishes provider assessments from verification.

## Native CLI execution

Hermes uses `chat -Q -q <brief> --max-turns 4`; OpenClaw uses `agent --message <brief>`. Executables are located by checking known executable paths; discovery no longer starts a blocking `which` subprocess. Arguments are passed directly, never through a shell.

Both use one shared runner with:

- 120-second elapsed-time deadline;
- 2 MiB combined stdout/stderr limit;
- concurrently drained, nonblocking pipes;
- an owned process group created atomically with `posix_spawn`;
- cancellation, deadline and output-limit cleanup confined to that process group;
- explicit launch/nonzero-exit errors, bounded diagnostic text and child reaping.

The blocking collection loop runs on a background queue; no mutable stdout/stderr variables are captured by competing closures. Fixtures verify normal output, nonzero exit, missing executable, output flood, timeout cleanup of an owned descendant and task cancellation. These are controlled process tests, not evidence that every installed agent command/version supports the selected arguments.

## HTTP and readiness

Hermes API handoffs use a separate ephemeral session with a 120-second total resource limit, 2 MiB response limit and no redirects. Credentials and the story brief are not forwarded to a redirect destination. Fixture HTTP tests verify success, oversized responses and redirect rejection.

Hermes API probing requires a 2xx response; 401/403 are not readiness. Disabling CLI preference no longer presents an unavailable API as ready merely because a CLI exists. An OpenClaw gateway without the required CLI is shown as detected but not ready for the CLI-only send action. Agents can still pull from the local bridge. The port probe rejects out-of-range ports before converting them to UInt16 and synchronizes completion so timeout/state callbacks cannot resume twice.

For version 1.2.0, actual installed Hermes/OpenClaw model handoff remains unverified: those executables were absent on the checked hosts. Controlled protocol and failure tests passed; a mock response is not a real agent acceptance. See [release limits](RELEASE_NOTES_1.2.0.md).
