# Agent News Bot for Apple Silicon

This download requires **macOS 14 or later and an Apple Silicon Mac**. It does not
need Rosetta. Intel users can build from source with `./build.sh --arch x86_64`;
the downloadable app in this release is ARM64 only.

**Signing: ad-hoc. Apple notarization: not performed.** This is an explicitly
labeled community build, not a Developer ID-signed/notarized download. Its signature
checks local bundle integrity; it does not establish an Apple-verified publisher.
The project owner selected this distribution format for this release.

1. Download only from the ShadowfetchStudio/agent-news-bot GitHub release.
2. Download `SHA256SUMS` alongside the ZIP and check its SHA-256 before extracting.
   The checksum confirms that the bytes match the release list; it is not an
   independent signature or a security assessment of the program.
3. Extract the ZIP. Quit an older Agent News Bot before installing. Keep a verified
   application backup before replacing a version with existing research.
4. Copy `Agent News Bot.app` to Applications. Do not delete the separate research
   home under `~/Library/Application Support/AgentNewsBot` during an upgrade.
5. Open it. Gatekeeper may block an unnotarized download. If you choose to trust
   this build after checking its origin, use Apple's documented **Privacy &
   Security → Open Anyway** process. This is a manual user decision; no script in
   this release disables Gatekeeper or removes quarantine attributes.
6. Finish source setup. Direct feeds work without a World Monitor account.
   Optional World Monitor and agent connections need their own running services
   and tests. No social post is sent automatically.

Apple's guidance: https://support.apple.com/en-us/102445

To inspect the unpacked app:

```sh
codesign --verify --deep --strict '/Applications/Agent News Bot.app'
file '/Applications/Agent News Bot.app/Contents/MacOS/AgentNewsBot'
```

Build source and architecture are recorded in
`Contents/Resources/BuildInfo.json`. Recovery/retention/Operations guides and
standalone Python recovery helpers are also included in Resources. Those helpers
need Python 3; the native news app itself does not require a Python installation.

Automatic refresh runs only while the app is running. Quitting stops its local
API and refresh loop. Linux provides the separate headless systemd service.
