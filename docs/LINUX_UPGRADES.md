# Linux installation, private configuration and recovery

## Install

From the `linux` folder of a trusted release:

```sh
sudo ./install.sh
```

Requires Python 3.10+ with venv/pip support, systemd and network access to the package index. The default system installation uses `/opt/agent-news-bot`, `/var/lib/agent-news-bot`, `/etc/agent-news-bot/config.env`, and a dedicated `agentnewsbot` account. It writes the CLI to `/usr/local/bin/agent-newsbot`.

For an unprivileged systemd user service:

```sh
./install.sh --user
```

This uses `~/.local/opt/agent-news-bot`, `~/.local/share/agent-news-bot`, `~/.config/agent-news-bot/config.env`, and `~/.local/bin`. Ensure the latter is on PATH. A user service normally follows the login/session lifecycle; enabling user lingering for boot-without-login is a separate administrative choice, not silently changed by this installer.

Options: `--prefix`, `--state`, `--config`, `--unit-dir`, `--bin-dir`, `--service`, `--service-user`, `--user`, `--no-start`, `--rollback`. Legacy `PREFIX`, `STATE_DIR`, `SVC_USER` environment overrides remain supported. Paths may contain spaces; newline-containing paths are rejected. Service names are validated. If the config declares `AGENT_NEWSBOT_HOME`, it must agree with `--state` so the backup targets the correct journal.

`--no-start` prepares and switches a release without stopping/starting/enabling a service or validating its health. It prints that limitation and must not be described as a verified running deployment.

## What an upgrade does

1. An installer lock prevents concurrent installer processes for this prefix.
2. A new permanent directory under `releases/` receives its own venv and installed package. Dependencies and imports are checked, including the X parser and SQLite JSON support. Failure here leaves the running release untouched.
3. The service is stopped for activation. Its story journal is backed up with SQLite's backup API, checked for integrity, and stored privately under `recovery/<transaction>/`. This includes consumer positions, source history, watchlists and editorial drafts. Tokens and configuration are not included in that database copy.
4. Existing main-unit bytes and settings are preserved. A managed runtime drop-in points ExecStart at `current/.venv/bin/python`, supplies the chosen data folder, clears the old PYTHONPATH and loads the private environment file. Unrelated drop-ins remain in place. A custom file occupying the managed drop-in path is refused rather than overwritten.
5. An atomic `current` symlink selects the prepared release. systemd reloads and starts the service. Acceptance requires an active service and `/health` reporting the same PID as systemd's MainPID. The effective service environment supplies the checked port and data directory, including preserved legacy environment files.
6. Only after successful health acceptance is startup enabled and the CLI switched. A custom unrelated CLI wrapper is preserved and reported. The previous staged release remains available.

Python venvs are built at their permanent location because their executable scripts contain absolute interpreter paths; they are not renamed after creation. See [Python's venv portability guidance](https://docs.python.org/3/library/venv.html). The unit and environment-file handling follow [systemd's execution configuration](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html). In particular, EnvironmentFile uses its own path parsing; it is not emitted using the quoting rules for ExecStart arguments.

## Automatic and explicit rollback

If activation/startup/health acceptance fails, the installer restores the previous current link and runtime drop-in, preserves the original unit/CLI, and restarts the previously active service. Installation returns failure; it never prints success for an unhealthy new release. If the old service cannot restart, the error identifies the recovery-record directory for inspection.

To switch back after a successful staged upgrade, repeat the same installation paths/service options with `--rollback`:

```sh
sudo ./install.sh --rollback
# or, for the user service:
./install.sh --user --rollback
```

This selects the previous prepared release, applies the same backup and health checks, and retains distinct recovery records. The successful current/previous pair can be switched again. It does not restore an older database automatically: that would discard work acknowledged after the upgrade. Current schema additions are additive; future incompatible schema changes must add an explicit migration/restore procedure before release.

Legacy in-place installations have no staged `previous` release until the first staged upgrade succeeds. Their original files/unit are preserved, and a failed initial migration restores the old unit/runtime. `--rollback` requires a previous **staged** release. Old legacy directories are not deleted automatically.

Recovery copies are not a full disaster-recovery system. They cover the story journal at activation, not configuration/credentials, feed cache, all snapshot files, or off-machine redundancy. Dedicated backup/restore and retention tooling remain a separate release-checklist item. No automatic cleanup of old release/recovery directories currently occurs; inspect storage and preserve the version/data needed for recovery before removing anything.

## Private settings

New installations create a private file with basic defaults. Existing files are never rewritten during upgrade. For a legacy unit, a newly created file contains only a comment so it does not override existing environment settings with new defaults.

```text
AGENT_NEWSBOT_PORT=18765
AGENT_NEWSBOT_FETCH_INTERVAL=900
AGENT_NEWSBOT_WEATHER_CITY="New York"
AGENT_NEWSBOT_WORLDMONITOR_ENABLED=0
```

For WorldMonitor, the supported additional keys are `AGENT_NEWSBOT_WORLDMONITOR_URL`, `AGENT_NEWSBOT_WORLDMONITOR_VARIANT`, and `AGENT_NEWSBOT_WORLDMONITOR_KEY`. Enter actual secrets locally; do not commit them or copy them into logs. Set permissions to 0600. The default system file is owned by the service account so that account's CLI can read the same settings.

The service manager loads the file through EnvironmentFile. Direct/CLI runs load `AGENT_NEWSBOT_CONFIG`, or default to `~/.config/agent-news-bot/config.env`. Set the variable to an empty string to disable file loading. Explicit process environment values take precedence in the Python loader. File contents are never sourced as shell code: only `AGENT_NEWSBOT_*` assignments, quoted single-line values and full-line comments are supported. There is no command/variable expansion, multiline values, `export`, inline comments or arbitrary environment keys. Invalid files fail without partially applying their settings. Restrict file ownership to the current user/root; administrative root can inspect the explicitly selected service-account-owned file.

Restart after editing service settings:

```sh
sudo systemctl restart agent-news-bot.service
sudo -u agentnewsbot agent-newsbot consumers
```

Use `systemctl --user` and omit sudo for a user installation. The bridge binds to localhost; configuration does not publish it on the network.

## File protections and validation

CLI runs set umask 0077. Newly created application data directories are 0700, database files are prepared as 0600 before SQLite creates sidecars, and database symlinks are rejected. Existing database permissions are tightened without replacing their contents. Bridge tokens are created exclusively at 0600; an invalid existing token fails visibly rather than being silently overwritten. Research-export directories/files retain 0700/0600 permissions.

Native Linux validation uses `tests/manual/native_install_smoke.py`. It creates a uniquely named systemd **user** service and isolated temporary paths, verifies installation/upgrade/configuration/consumer preservation, exercises explicit rollback, and injects a broken prepared release to verify automatic rollback. It removes only its own service afterward and leaves private recovery artifacts and a JSON report for inspection. This is not a root-system-service acceptance test; unit generation and preservation/rollback behavior are also covered by automated tests.
