"""Private application-data archives; no activation, credential extraction or network calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
from datetime import datetime, timezone
try:
    from . import recovery
except ImportError:
    import recovery

FORMAT = 'agent-newsbot-application-backup'
MANIFEST_LIMIT = 8 * 1024 * 1024
MAX_ENTRIES = 10000
CONFIG_NAMES = {'macos': 'settings.plist', 'linux': 'config.env'}
EXCLUDED = ['Mac Keychain credentials', 'bridge.token and temporary bridge credentials',
            'unselected external files and agent inboxes/outboxes', 'binaries and OS/service installation']


def check_time(deadline):
    if time.monotonic() >= deadline:
        raise recovery.RecoveryError('Application recovery deadline reached')


def root(path):
    path = Path(path).absolute()
    recovery.directory(path)
    return path.resolve()


def entry_name(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise recovery.RecoveryError('Invalid archive path')
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(part in ('..', '.') for part in p.parts):
        raise recovery.RecoveryError('Invalid archive path')
    return value


def excluded(name):
    return name == 'bridge.token' or name.startswith('.bridge-token-')


def inventory(folder, deadline, *, omit_credentials=False, max_depth=32):
    files, directories, omitted = {}, [], []
    def visit(current, depth):
        check_time(deadline)
        if depth > max_depth:
            raise recovery.RecoveryError('Archive directory nesting exceeds supported depth')
        for path in sorted(current.iterdir()):
            check_time(deadline)
            rel = entry_name(path.relative_to(folder).as_posix())
            if len(files) + len(directories) + len(omitted) >= MAX_ENTRIES:
                raise recovery.RecoveryError('Archive tree exceeds 10000 entries')
            if omit_credentials and excluded(path.name):
                omitted.append(rel)
                continue
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.append(rel); visit(path, depth + 1)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files[rel] = fingerprint(info)
            else:
                raise recovery.RecoveryError('Archive inputs must be real directories and unlinked regular files')
    visit(folder, 0)
    return files, sorted(directories), sorted(omitted)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def copy_file(source, target, remaining, deadline, expected=None):
    check_time(deadline)
    recovery.regular(source, remaining)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as src:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise recovery.RecoveryError('Source is not an unlinked regular file')
        value, size = hashlib.sha256(), 0
        with recovery.private_file(target) as dst:
            while block := src.read(1024 * 1024):
                check_time(deadline)
                size += len(block)
                if size > remaining or shutil.disk_usage(target.parent).free < recovery.RESERVE:
                    raise recovery.RecoveryError('Application archive exceeds byte budget or free-space reserve')
                value.update(block); dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        if fingerprint(before) != fingerprint(os.fstat(src.fileno())) or fingerprint(before) != fingerprint(source.lstat()):
            raise recovery.RecoveryError('Input changed during copy; stop writers and retry into a new folder')
    result = dict(bytes=size, sha256=value.hexdigest())
    if expected is not None and result != expected:
        raise recovery.RecoveryError('Application archive changed during restore')
    return result


def destination(path, inputs=()):
    path = Path(path).absolute()
    parent = root(path.parent)
    path = parent / path.name
    if any(path == p or p in path.parents for p in inputs):
        raise recovery.RecoveryError('Destination must be outside every selected source directory')
    path.mkdir(mode=0o700)
    recovery.write_json(path / 'INCOMPLETE.json', {'operation': 'application recovery', 'format': FORMAT})
    return path


def finish(target, markers):
    try:
        recovery.sync_directory(target)
        for marker in markers:
            marker.unlink()
        recovery.sync_directory(target)
        recovery.sync_directory(target.parent)
    except BaseException:
        for marker in markers:
            if not os.path.lexists(marker):
                try:
                    recovery.write_json(marker, {'operation':'application finalization failed'})
                except OSError:
                    pass  # A failed storage device cannot guarantee a new marker write.
        raise


def backup(home, target, *, platform, config, exports, writers_stopped=False,
           max_bytes=recovery.DEFAULT_LIMIT, timeout=60):
    deadline = recovery.bounds(max_bytes, timeout)
    if writers_stopped is not True:
        raise recovery.RecoveryError('Stop app/service and external writers, then specify --writers-stopped')
    if platform not in CONFIG_NAMES or not isinstance(exports, dict) or len(exports) > 32:
        raise recovery.RecoveryError('Invalid platform or export roots (maximum 32)')
    home = root(home)
    config = Path(config).absolute()
    recovery.regular(config, 65536)
    config_before = fingerprint(config.lstat())
    roots = {}
    for label, folder in exports.items():
        if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', label):
            raise recovery.RecoveryError('Export labels require 1-64 letters, digits, dash or underscore')
        roots[label] = root(folder)
    source_inventories = {label: inventory(folder, deadline, omit_credentials=True, max_depth=30) for label, folder in roots.items()}
    target = destination(target, [home, *roots.values()])
    research = recovery.backup(home, target / 'research', max_bytes=max_bytes, timeout=max(1, deadline-time.monotonic()))
    research.pop('folder')
    remaining = max_bytes - sum(e['bytes'] for e in research['files'].values())
    directories = ['configuration', 'exports']
    for label, (_, dirs, _) in source_inventories.items():
        directories += ['exports/' + label] + ['exports/' + label + '/' + d for d in dirs]
    if len(directories) + sum(len(v[0]) for v in source_inventories.values()) + 1 > MAX_ENTRIES - 16:
        raise recovery.RecoveryError('Application archive exceeds entry budget')
    for name in sorted(directories):
        (target / name).mkdir(mode=0o700)
    files = {}
    config_name = 'configuration/' + CONFIG_NAMES[platform]
    files[config_name] = copy_file(config, target / config_name, min(65536, remaining), deadline)
    remaining -= files[config_name]['bytes']
    for label, (items, _, _) in source_inventories.items():
        for rel in sorted(items):
            name = 'exports/' + label + '/' + rel
            files[name] = copy_file(roots[label] / rel, target / name, remaining, deadline)
            remaining -= files[name]['bytes']
    if config_before != fingerprint(config.lstat()) or any(inventory(roots[label], deadline, omit_credentials=True, max_depth=30) != inv for label, inv in source_inventories.items()):
        raise recovery.RecoveryError('Inputs changed during archive; stop writers and retry into a new folder')
    manifest = dict(format=FORMAT, version=1, platform=platform, created_at=datetime.now(timezone.utc).isoformat(),
                    consistency='Operator stopped writers; per-database snapshots and change-checked file copies, not an atomic filesystem snapshot',
                    research=research, files=files, directories=sorted(directories),
                    omitted_export_credentials={label: inv[2] for label, inv in source_inventories.items() if inv[2]},
                    excluded=EXCLUDED, configuration_may_contain_secrets=True)
    check_time(deadline)
    if len(json.dumps(manifest, indent=2, sort_keys=True).encode()) + 1 > MANIFEST_LIMIT:
        raise recovery.RecoveryError('Application manifest exceeds 8 MiB')
    recovery.write_json(target / 'manifest.json', manifest)
    finish(target, [target / 'INCOMPLETE.json'])
    return dict(folder=str(target), **manifest)


def verify(archive, *, max_bytes=recovery.DEFAULT_LIMIT, timeout=60):
    deadline = recovery.bounds(max_bytes, timeout)
    archive = root(archive)
    recovery.regular(archive / 'manifest.json', MANIFEST_LIMIT)
    manifest = json.loads((archive / 'manifest.json').read_bytes())
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or type(manifest.get('version')) is not int or manifest['version'] != 1 or manifest.get('platform') not in CONFIG_NAMES:
        raise recovery.RecoveryError('Unsupported application archive')
    files, directories = manifest.get('files'), manifest.get('directories')
    if not isinstance(files, dict) or not isinstance(directories, list) or len(files) + len(directories) > MAX_ENTRIES:
        raise recovery.RecoveryError('Invalid application archive inventory')
    config_name = 'configuration/' + CONFIG_NAMES[manifest['platform']]
    if config_name not in files or not all(isinstance(v, str) for v in directories) or len(set(directories)) != len(directories):
        raise recovery.RecoveryError('Missing configuration or invalid directories')
    for name in [*files, *directories]:
        entry_name(name)
        if name not in ('configuration', 'exports', config_name) and not name.startswith('exports/'):
            raise recovery.RecoveryError('Unexpected application archive path')
        if excluded(PurePosixPath(name).name):
            raise recovery.RecoveryError('Bridge credentials cannot be restored from application archives')
    if set(files) & set(directories) or not {'configuration', 'exports'} <= set(directories):
        raise recovery.RecoveryError('Invalid directory structure')
    actual_files, actual_dirs, _ = inventory(archive, deadline)
    research = recovery.verify(archive / 'research', max_bytes=max_bytes, timeout=max(1, deadline-time.monotonic()))
    if research != manifest.get('research'):
        raise recovery.RecoveryError('Research manifest differs from application archive')
    expected_files = set(files) | {'manifest.json', 'research/manifest.json'} | {'research/' + f for f in research['files']}
    if set(actual_files) != expected_files or set(actual_dirs) != set(directories) | {'research'}:
        raise recovery.RecoveryError('Unexpected, missing or incomplete application archive entries')
    remaining = max_bytes - sum(e['bytes'] for e in research['files'].values())
    for name, expected in files.items():
        if not isinstance(expected, dict) or set(expected) != {'bytes', 'sha256'} or type(expected['bytes']) is not int or expected['bytes'] < 0 or not isinstance(expected['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', expected['sha256']):
            raise recovery.RecoveryError('Invalid application file metadata')
        size, sha = recovery.digest(archive / name, min(remaining, 65536) if name == config_name else remaining, deadline)
        if dict(bytes=size, sha256=sha) != expected:
            raise recovery.RecoveryError('Application archive checksum mismatch')
        remaining -= size
    check_time(deadline)
    return manifest


def restore(archive, target, *, max_bytes=recovery.DEFAULT_LIMIT, timeout=60):
    deadline = recovery.bounds(max_bytes, timeout)
    manifest = verify(archive, max_bytes=max_bytes, timeout=timeout)
    archive = root(archive)
    target = destination(target, [archive])
    try:
        for name in sorted(manifest['directories']):
            (target / name).mkdir(mode=0o700)
        remaining = max_bytes
        for name, expected in manifest['files'].items():
            copy_file(archive / name, target / name, remaining, deadline, expected)
            remaining -= expected['bytes']
        # Validate again before restoring any usable research home. Nothing is activated.
        if verify(archive, max_bytes=max_bytes, timeout=max(1, deadline-time.monotonic())) != manifest:
            raise recovery.RecoveryError('Application archive changed during restore')
        report = recovery.restore(archive / 'research', target / 'home', max_bytes=max_bytes,
                                  timeout=max(1, deadline-time.monotonic()))
        recovery.write_json(target / 'home/RECOVERY_INCOMPLETE.json', {'operation':'application recovery'})
        check_time(deadline)
        receipt = dict(format=FORMAT, version=1, platform=manifest['platform'], files=manifest['files'],
                       directories=manifest['directories'], research=report, excluded=EXCLUDED,
                       restored_at=datetime.now(timezone.utc).isoformat(), activated=False,
                       configuration_warning='Review configuration and old absolute paths; re-enter Keychain credentials and reconnect consumers with a new bridge token.')
        recovery.write_json(target / 'APPLICATION_RECOVERY.json', receipt)
        finish(target, [target / 'home/RECOVERY_INCOMPLETE.json', target / 'INCOMPLETE.json'])
        return dict(folder=str(target), **receipt)
    except BaseException:
        if (target / 'home').is_dir() and not (target / 'home/RECOVERY_INCOMPLETE.json').exists():
            recovery.write_json(target / 'home/RECOVERY_INCOMPLETE.json', {'operation':'failed application recovery'})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('backup-application')
    create.add_argument('--home', required=True); create.add_argument('--config', required=True)
    create.add_argument('--platform', choices=sorted(CONFIG_NAMES), required=True)
    create.add_argument('--destination', required=True); create.add_argument('--writers-stopped', action='store_true')
    choice = create.add_mutually_exclusive_group(required=True)
    choice.add_argument('--exports', action='append', metavar='LABEL=FOLDER')
    choice.add_argument('--no-exports', action='store_true')
    check = sub.add_parser('verify-application'); check.add_argument('--backup', required=True)
    recover = sub.add_parser('restore-application'); recover.add_argument('--backup', required=True); recover.add_argument('--destination', required=True)
    for cmd in (create, check, recover):
        cmd.add_argument('--max-bytes', type=int, default=recovery.DEFAULT_LIMIT)
        cmd.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        options = dict(max_bytes=args.max_bytes, timeout=args.timeout)
        if args.command == 'backup-application':
            exports = {}
            for value in args.exports or []:
                label, separator, folder = value.partition('=')
                if not separator or not folder or label in exports:
                    raise recovery.RecoveryError('Export inputs require unique LABEL=FOLDER values')
                exports[label] = folder
            result = backup(args.home, args.destination, platform=args.platform, config=args.config, exports=exports,
                            writers_stopped=args.writers_stopped, **options)
        elif args.command == 'verify-application':
            result = verify(args.backup, **options)
        else:
            result = restore(args.backup, args.destination, **options)
        # Never print the configuration contents or private local source paths.
        json.dump(result, sys.stdout, indent=2); sys.stdout.write('\n'); return 0
    except recovery.RecoveryError as exc:
        print(f'Application recovery failed: {exc}. Existing data is unchanged; do not activate the destination.', file=sys.stderr)
        return 1
    except (OSError, ValueError, recovery.sqlite3.Error):
        print('Application recovery failed validation, input stability, storage or time/size limits. Existing data is unchanged; do not activate an incomplete destination.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
