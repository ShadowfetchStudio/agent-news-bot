# Shared X parser build

This development-only tool produces the single JS bundle shipped in both the Python wheel and Mac app. Node/npm/esbuild are not runtime dependencies.

From this directory:

```sh
npm ci --ignore-scripts
node build.mjs
```

Inputs are locked in `package-lock.json`; output and SHA256 are committed under `linux/agent_newsbot/vendor/`. `build.sh` copies those exact bytes and third-party licenses into the Mac app. Setuptools includes them in the Linux package. Tests compare both engines against 24 official v3 weighted/Unicode cases and composed-draft cases.

Upstream: Twitter's Apache-2.0 `twitter-text` 3.1.0, MIT `twemoji-parser` 11.0.2, MIT `punycode` 1.4.1. Copies of licenses accompany the bundle. We use only text parsing; no Twemoji image assets are included.

Explicit build adaptations:

1. Remove core-js v2 side-effect polyfill imports from upstream compiled modules. Both shipping engines already implement the required ES2018 features; the obsolete polyfills are not shipped.
2. In `extractUrlsWithIndices`, replace non-standard `RegExp.$n`/`lastMatch` access with the corresponding explicit match-array indices. QuickJS does not provide those legacy globals. URL patterns and matching behavior remain upstream's. Conformance tests exercise the adaptation.
3. `entry.js` is our wrapper for JSON calls, normalized draft composition, whole-tag budget selection and entity-safe truncation. The upstream weighted parser validates the final result.

Do not silently replace this with a character count when loading/execution fails. Draft creation fails closed; source articles remain available. The parser version includes a fixed emoji/TLD dataset, so new platform behavior may require an explicit dependency and fixture update.
