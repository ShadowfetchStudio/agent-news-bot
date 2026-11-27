#!/bin/sh
# System installation by default; --user supports an unprivileged user service.
set -eu
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/installer.py" "$@"
